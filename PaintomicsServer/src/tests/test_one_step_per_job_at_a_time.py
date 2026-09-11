#!/usr/bin/env python3
"""One job may have only one step in the queue at a time.

Why this exists
---------------
This is the invariant that makes sharing a job instance safe, so it deserves a
test of its own rather than being left as a comment.

`loadJobInstance` was changed (68faf35b's sibling, in JobInformationManager) to
hand every concurrent caller the *same* `PathwayAcquisitionJob` object instead
of loading a private copy each. That closed a lost update: two requests holding
separate copies each stored theirs, and the later write erased the earlier one.

But sharing raises the opposite question, and it is the one worth checking:
if two requests now hold one mutable object, can two of them mutate it at once?
Step 2 is not a read -- it does

    jobInstance.updateSubmitedCompoundsList(selectedCompounds)
    jobInstance.generateMetagenesList(ROOT_DIRECTORY, clusterNumber)
    jobInstance.setLastStep(3)
    JobInformationManager().storeJobInstance(jobInstance, 2)

so two concurrent step 2s on one job would interleave those writes on a single
instance. Before the change they would have corrupted two separate objects,
which is bad differently, not less.

They cannot, and the reason is `Queue.enqueue`. Step 1 and step 2 both enqueue
under `job_id=jobID` -- the job's own id, not a per-step one -- so the second
submission for a job that is still queued or running is refused:

    RuntimeError: Job already at the queue (Job id : ...)

Checked against the running server: two simultaneous POSTs to /pa_step2 for one
job returned HTTP 200 and HTTP 400, the 400 carrying exactly that message.

And the guard is atomic, which is the part that actually matters. `enqueue`
takes `self.lock` before testing `job_id in self.jobs` and does not release it
until after `self.jobs[job_id] = job`, so the test and the insert cannot be
split. That is what distinguishes it from the five check-then-act bugs fixed
this session, every one of which tested shared state under a lock, released it,
and acted on a conclusion that had already expired.

So this file does not fix anything. It pins the property the fix depends on: if
someone later narrows that lock to just the dictionary write -- an entirely
reasonable-looking change, since the dictionary is what needs protecting -- the
duplicate guard silently stops working and concurrent step 2s start interleaving
on one shared instance. Nothing else in the suite would notice.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_one_step_per_job_at_a_time
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common.PySiQ import Queue, JobStatus


def _work():
    return "done"


class _QueueCase(unittest.TestCase):

    def setUp(self):
        # A fresh Queue, bypassing the Singleton metaclass so tests do not
        # inherit each other's state or the server's.
        self.queue = Queue.__new__(Queue)
        self.queue.lock = threading.RLock()
        self.queue.jobs = {}
        self.queue.queue = []
        self.queue.workers = []
        self.queue.notify_workers = lambda: None

    def _enqueueConcurrently(self, jobID, n):
        """Every thread submits the same job id at the same moment."""
        ready = threading.Barrier(n)
        accepted, refused = [], []
        listLock = threading.Lock()

        def submit():
            ready.wait()
            try:
                self.queue.enqueue(fn=_work, args=(), job_id=jobID)
                with listLock:
                    accepted.append(1)
            except RuntimeError as exc:
                with listLock:
                    refused.append(str(exc))

        threads = [threading.Thread(target=submit) for _ in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return accepted, refused


class OneStepPerJobTest(_QueueCase):

    def test_only_one_of_many_simultaneous_submissions_is_accepted(self):
        accepted, refused = self._enqueueConcurrently("JOB1", 8)

        self.assertEqual(len(accepted), 1,
                         "%d of 8 simultaneous submissions for one job were "
                         "accepted; each accepted one becomes a worker mutating "
                         "the same shared job instance" % len(accepted))
        self.assertEqual(len(refused), 7)

    def test_the_refusal_says_why(self):
        _, refused = self._enqueueConcurrently("JOB1", 4)

        self.assertTrue(all("already at the queue" in message
                            for message in refused),
                        "refusals should name the cause: %s" % refused[:2])

    def test_the_queue_holds_one_entry_for_the_job(self):
        """A duplicate must not be appended even if it were tolerated."""
        self._enqueueConcurrently("JOB1", 8)

        queued = [job for job in self.queue.queue if job.id == "JOB1"]
        self.assertEqual(len(queued), 1,
                         "the run queue holds %d copies of one job, so it would "
                         "be executed %d times" % (len(queued), len(queued)))

    def test_different_jobs_are_all_accepted(self):
        """The guard must be per job, not a global one-at-a-time lock."""
        results = []
        listLock = threading.Lock()
        ready = threading.Barrier(4)

        def submit(jobID):
            ready.wait()
            self.queue.enqueue(fn=_work, args=(), job_id=jobID)
            with listLock:
                results.append(jobID)

        threads = [threading.Thread(target=submit, args=(j,))
                   for j in ("A", "B", "C", "D")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), ["A", "B", "C", "D"],
                         "submissions for different jobs must not block one "
                         "another")

    def test_a_finished_job_can_be_submitted_again(self):
        """Otherwise a job could never advance from step 1 to step 2.

        Both steps enqueue under the same id, so step 2's submission would be
        refused forever if a finished entry were never cleared.
        """
        self.queue.enqueue(fn=_work, args=(), job_id="JOB1")
        self.queue.jobs["JOB1"].status = JobStatus.FINISHED
        self.queue.jobs["JOB1"].result = "step1 result"

        self.queue.enqueue(fn=_work, args=(), job_id="JOB1")

        self.assertIn("JOB1", self.queue.jobs)
        self.assertNotEqual(self.queue.jobs["JOB1"].status, JobStatus.FINISHED,
                            "the new submission should be pending, not the "
                            "finished entry left in place")

    def test_a_started_job_is_not_displaced(self):
        """The refusal must cover STARTED, not only QUEUED.

        A step midway through generateMetagenesList is exactly when a second
        mutating caller does the most damage.
        """
        self.queue.enqueue(fn=_work, args=(), job_id="JOB1")
        self.queue.jobs["JOB1"].status = JobStatus.STARTED
        started = self.queue.jobs["JOB1"]

        with self.assertRaises(RuntimeError):
            self.queue.enqueue(fn=_work, args=(), job_id="JOB1")

        self.assertIs(self.queue.jobs["JOB1"], started,
                      "the running job was replaced in the registry")

    def test_a_failed_job_can_be_retried(self):
        """A failed run is over, so its id must not stay blocked.

        Found while writing the test above: `JobStatus` has FAILED as well as
        FINISHED, and only FINISHED was being cleared. Nothing else removes a
        failed entry except the status poll, so if the client stopped polling
        before it saw the failure -- closed tab, dropped connection -- the id
        was blocked until the server restarted, and every retry was told the job
        was "already at the queue" when nothing was queued at all.
        """
        self.queue.enqueue(fn=_work, args=(), job_id="JOB1")
        self.queue.jobs["JOB1"].status = JobStatus.FAILED
        self.queue.jobs["JOB1"].error_message = "R script exited 1"

        self.queue.enqueue(fn=_work, args=(), job_id="JOB1")

        self.assertEqual(self.queue.jobs["JOB1"].status, JobStatus.QUEUED,
                         "the retry did not replace the failed entry")

    def test_a_failed_job_that_was_polled_can_also_be_retried(self):
        """The path that already worked must keep working.

        When the client does observe the failure it acknowledges it, which
        removes the entry, so the retry finds nothing there. (The status poll
        used to consume the entry itself, through get_result; it now delivers
        without consuming and the client's acknowledgement does the removing.
        Either way the entry is gone before the retry.)
        """
        self.queue.enqueue(fn=_work, args=(), job_id="JOB1")
        self.queue.jobs["JOB1"].status = JobStatus.FAILED
        self.queue.deliver_result("JOB1")       # what the status poll does
        self.queue.acknowledge("JOB1")          # what the client does with the answer

        self.assertNotIn("JOB1", self.queue.jobs)
        self.queue.enqueue(fn=_work, args=(), job_id="JOB1")
        self.assertEqual(self.queue.jobs["JOB1"].status, JobStatus.QUEUED)


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
