#!/usr/bin/env python3
"""A finished job's result outlives the one HTTP response that carried it.

Why this exists
---------------
`Queue.get_result` pops a FINISHED or FAILED job the first time it is read,
and `checkJobStatus` used it, so the single response carrying a job's result
was the only copy that would ever exist. Lose that response -- an nginx 504
on a 61 s poll, a dropped connection, a laptop lid -- and the next poll was
told

    Your job is not on the queue anymore. Check your job list, if it's not
    there the process stopped and you must resend the data again.

for a job that had just succeeded. That is the server half of the 2026-09-11
incident on paintomics.uv.es (the client half, the poll chain dying on the
504, is test_status_poll_retries_unreadable_answers). A client that retries
its poll is only useful if the retry can still find the result.

Pinned here:

  * deliver_result hands the result out and leaves the entry in place, so a
    second read gets the same result;
  * acknowledge removes it, which is what the client does once the result is
    in hand -- and only for the delivery its token names, so a slow
    acknowledgement for step 1 cannot remove step 2's result under the same
    job id (the review finding on #155);
  * reap_delivered removes a delivered result after DELIVERED_RESULT_TTL and
    not before, and never touches a result nobody has been given;
  * get_result keeps its consuming semantics for its remaining callers, and
    enqueue() with the same id still clears a finished run;
  * the status handler reads through deliver_result on both terminal
    branches and never through get_result, and the ack route exists.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_finished_job_result_survives_a_lost_response
"""
import collections
import inspect
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common.PySiQ import Job, JobStatus, Queue


class _Result:
    def __init__(self, tag="OK"):
        self.tag = tag

    def getResponse(self):
        return self.tag


def _queue():
    queue = Queue.__new__(Queue)
    queue.lock = threading.Lock()
    queue.queue = collections.deque([])
    queue.jobs = {}
    queue.workers = []
    return queue


def _add(queue, jobID, status, result=None, error=None):
    job = Job(lambda: None, ())
    job.set_id(jobID)
    job.status = status
    job.result = result
    job.error_message = error
    queue.jobs[jobID] = job
    return job


class DeliveryIsNotConsumptionTest(unittest.TestCase):

    def setUp(self):
        self.queue = _queue()
        self.result = _Result()
        _add(self.queue, "J1", JobStatus.FINISHED, self.result)

    def test_a_delivered_result_can_be_delivered_again(self):
        """The retry after a lost response."""
        first = self.queue.deliver_result("J1")
        second = self.queue.deliver_result("J1")

        self.assertIs(first, self.result)
        self.assertIs(second, self.result,
                      "the second poll got %r: the first delivery consumed "
                      "the result, so a lost response still loses the job" % (second,))
        self.assertIn("J1", self.queue.jobs)

    def test_delivery_stamps_the_first_time_only(self):
        self.assertIsNone(self.queue.jobs["J1"].delivered_at)
        self.queue.deliver_result("J1")
        stamp = self.queue.jobs["J1"].delivered_at
        self.assertIsNotNone(stamp)
        self.queue.deliver_result("J1")
        self.assertEqual(self.queue.jobs["J1"].delivered_at, stamp,
                         "a re-delivery moved the stamp, so a client polling "
                         "every few seconds would keep the result alive for ever")

    def test_a_running_job_is_not_delivered(self):
        _add(self.queue, "J2", JobStatus.STARTED)
        self.assertEqual(self.queue.deliver_result("J2"), JobStatus.STARTED)
        self.assertIsNone(self.queue.jobs["J2"].delivered_at)

    def test_an_unknown_job_is_not_queued(self):
        self.assertEqual(self.queue.deliver_result("nope"), JobStatus.NOT_QUEUED)

    def test_a_failed_job_is_delivered_the_same_way(self):
        """A retry after a lost 400 must still learn WHY the job failed."""
        _add(self.queue, "J3", JobStatus.FAILED, error="R script exited 1")
        self.queue.deliver_result("J3")
        self.queue.deliver_result("J3")
        self.assertIn("J3", self.queue.jobs)
        self.assertEqual(self.queue.jobs["J3"].error_message, "R script exited 1")

    def test_two_pollers_at_once_both_get_the_result(self):
        """Two tabs on one job. The old code handed the loser a JobStatus
        enum and the handler 500ed on it; now both draw the same job."""
        barrier = threading.Barrier(2)
        served, errors = [], []

        def poll():
            try:
                barrier.wait()
                served.append(self.queue.deliver_result("J1"))
            except Exception as exc:
                errors.append("%s: %s" % (type(exc).__name__, exc))

        threads = [threading.Thread(target=poll) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(served, [self.result, self.result])


class AcknowledgementTest(unittest.TestCase):

    def setUp(self):
        self.queue = _queue()
        _add(self.queue, "J1", JobStatus.FINISHED, _Result())

    def test_a_delivery_has_a_token(self):
        self.assertIsNone(self.queue.delivery_token("J1"))
        self.queue.deliver_result("J1")
        token = self.queue.delivery_token("J1")
        self.assertTrue(token)
        self.queue.deliver_result("J1")
        self.assertEqual(self.queue.delivery_token("J1"), token,
                         "a re-delivery renamed the delivery, so the token the "
                         "first answer carried no longer acknowledges anything")

    def test_acknowledging_a_delivered_result_by_its_token_removes_it(self):
        self.queue.deliver_result("J1")
        token = self.queue.delivery_token("J1")
        self.assertTrue(self.queue.acknowledge("J1", token))
        self.assertNotIn("J1", self.queue.jobs)
        self.assertEqual(self.queue.deliver_result("J1"), JobStatus.NOT_QUEUED)

    def test_a_stale_acknowledgement_does_not_remove_the_next_runs_result(self):
        """The review finding on #155, as a sequence.

        Step 1 under J1 finishes and is delivered; the client fires its
        acknowledgement and, without waiting for it, submits step 2, which
        enqueue() files as a new Job under J1. Step 2 finishes before the
        slow acknowledgement lands. Keyed on the id alone that acknowledgement
        would pop step 2's result -- which the client has never been given --
        and the next poll would be told the job is gone.
        """
        queue = Queue()
        queue.enqueue(fn=lambda: None, args=(), job_id="J1")
        queue.jobs["J1"].status = JobStatus.FINISHED
        queue.jobs["J1"].result = _Result("step 1")
        queue.deliver_result("J1")
        stale = queue.delivery_token("J1")

        queue.enqueue(fn=lambda: None, args=(), job_id="J1")     # step 2 filed
        queue.jobs["J1"].status = JobStatus.FINISHED
        queue.jobs["J1"].result = _Result("step 2")

        self.assertFalse(queue.acknowledge("J1", stale),
                         "step 1's acknowledgement removed step 2's result")
        self.assertIn("J1", queue.jobs)
        self.assertEqual(queue.deliver_result("J1").getResponse(), "step 2")

        # Even after step 2 has itself been delivered, the stale token names
        # nothing; only step 2's own does.
        self.assertFalse(queue.acknowledge("J1", stale))
        self.assertIn("J1", queue.jobs)
        self.assertTrue(queue.acknowledge("J1", queue.delivery_token("J1")))
        self.assertNotIn("J1", queue.jobs)

    def test_acknowledging_without_a_token_does_nothing(self):
        self.queue.deliver_result("J1")
        self.assertFalse(self.queue.acknowledge("J1", None))
        self.assertFalse(self.queue.acknowledge("J1", ""))
        self.assertIn("J1", self.queue.jobs)

    def test_acknowledging_an_undelivered_result_does_nothing(self):
        """No delivery, no token, nothing to match: the entry stays for the
        poll that has not arrived yet."""
        self.assertFalse(self.queue.acknowledge("J1", "anything"))
        self.assertIn("J1", self.queue.jobs)

    def test_acknowledging_a_running_job_does_nothing(self):
        """A stale or guessed id must not kill a job that is still running."""
        _add(self.queue, "J2", JobStatus.STARTED)
        self.assertFalse(self.queue.acknowledge("J2", "anything"))
        self.assertIn("J2", self.queue.jobs)

    def test_acknowledging_twice_is_harmless(self):
        self.queue.deliver_result("J1")
        token = self.queue.delivery_token("J1")
        self.queue.acknowledge("J1", token)
        self.assertFalse(self.queue.acknowledge("J1", token))

    def test_acknowledging_an_unknown_job_is_harmless(self):
        self.assertFalse(self.queue.acknowledge("nope", "anything"))
        self.assertIsNone(self.queue.delivery_token("nope"))


class ReaperTest(unittest.TestCase):

    def setUp(self):
        self.queue = _queue()
        _add(self.queue, "J1", JobStatus.FINISHED, _Result())

    # `delivered_at` is set here rather than taken from deliver_result(), and
    # that is the fix for a real CI flake rather than tidiness.
    #
    # deliver_result() stamps `time.monotonic()`, which on a fresh CI runner is
    # seconds since boot -- a few hundred. reap_delivered() then tests
    # `now - delivered_at >= ttl`, and the test handed it `now = stamp + 600`.
    # At that magnitude `stamp + 600` is not exactly representable, so
    # `(stamp + 600) - stamp` comes back as 599.99999999999989 about one time
    # in ten and the boundary case silently inverts. Measured over 200,000
    # stamps drawn from [100, 3000): 9.5% fail.
    #
    # It never reproduced on a developer machine, and the reason is the same
    # arithmetic: a laptop that has been up for weeks has a monotonic clock in
    # the millions, where the subtraction is exact. Testing at that magnitude
    # is what made this look like noise twice (pull requests #162 and #163).
    #
    # Whole numbers small enough to be exact keep the boundary meaningful --
    # the point of these two cases is that the comparison is `>=` and not `>`
    # -- without asking floating point for a guarantee it does not give.
    STAMP = 1000.0

    def _deliverAt(self, stamp):
        """Deliver, then replace the clock stamp with an exact one.

        The exactness check lives here rather than in a test of its own, so it
        covers every boundary case by construction: a stamp that reached these
        tests from a clock cannot get past this line.
        """
        self.assertEqual(stamp, int(stamp),
                         "the boundary cases need an integral stamp; %r came "
                         "from a clock, and the TTL comparison on it is a coin "
                         "flip" % (stamp,))
        self.assertLess(abs(stamp) + 600, 2.0 ** 53,
                        "integral stamps stop being exact above 2**53")
        self.queue.deliver_result("J1")
        self.queue.jobs["J1"].delivered_at = stamp

    def test_a_delivered_result_is_kept_inside_the_ttl(self):
        self._deliverAt(self.STAMP)
        removed = self.queue.reap_delivered(ttl=600, now=self.STAMP + 599)
        self.assertEqual(removed, [])
        self.assertIn("J1", self.queue.jobs)

    def test_a_delivered_result_is_dropped_after_the_ttl(self):
        self._deliverAt(self.STAMP)
        removed = self.queue.reap_delivered(ttl=600, now=self.STAMP + 600)
        self.assertEqual(removed, ["J1"])
        self.assertNotIn("J1", self.queue.jobs)

    def test_the_real_stamp_is_reaped_once_the_ttl_is_comfortably_past(self):
        """The stamp deliver_result() actually writes still works.

        Pinning the boundary with chosen numbers must not stop anything
        exercising `time.monotonic()`, or a stamp that was never set at all
        would pass both cases above.
        """
        self.queue.deliver_result("J1")
        stamp = self.queue.jobs["J1"].delivered_at
        self.assertIsNotNone(stamp)
        removed = self.queue.reap_delivered(ttl=600, now=stamp + 601)
        self.assertEqual(removed, ["J1"])

    def test_the_boundary_cases_use_a_stamp_floats_can_be_exact_about(self):
        """Guards the fix itself, and does so deterministically.

        The first version of this recomputed `(STAMP + offset) - STAMP` and
        checked it equalled the offset. That is the *same* rounding-sensitive
        expression the bug was about, so against a clock reading it would not
        fail -- it would fail about nine times in ten, which is a second flaky
        test standing next to the one it is meant to guard. Caught in review on
        pull request #164.

        What is asserted instead is a property of the stamp itself. An integral
        value below 2**53 is exactly representable, and so is that value plus
        any integer offset that stays below 2**53: no rounding is possible, for
        any offset, on any machine. A `time.monotonic()` reading is integral
        with probability nil, so reverting STAMP to one fails this every time.

        What it does NOT catch, stated because the limit is real: someone
        deleting `_deliverAt` and going back to reading `delivered_at` straight
        out of `deliver_result()`. STAMP would still be 1000.0 and this would
        still pass. That regression is guarded one level down -- the exactness
        check is inside `_deliverAt`, so the boundary cases cannot be fed a
        clock stamp through it -- and not at all if the helper is bypassed
        entirely, which no assertion here can see.
        """
        self.assertEqual(self.STAMP, int(self.STAMP),
                         "STAMP=%r is not integral, so the boundary cases are "
                         "back to asking floats for an exact answer"
                         % (self.STAMP,))
        self.assertLess(abs(self.STAMP) + 600, 2.0 ** 53,
                        "integral stamps stop being exact above 2**53")

    def test_a_messy_stamp_is_what_used_to_break_this(self):
        """The flake reproduced, so the diagnosis stays checkable.

        Not a claim about the reaper: at the exact TTL boundary a difference of
        1e-13 either way is meaningless, and in production `now` is a fresh
        clock reading rather than `stamp + ttl`, so a result missed by one poll
        is reaped by the next. It is a claim about the TEST -- that asking for
        `>=` at exactly the boundary is not something a float stamp can answer
        reliably, which is why the cases above no longer ask.
        """
        # A real fresh-runner-magnitude reading, found by search rather than
        # guessed -- a hand-picked "messy-looking" number does not reproduce it.
        # Pinned as a literal so this cannot itself become intermittent.
        stamp = 1773.5985509907462
        self.assertLess((stamp + 600) - stamp, 600.0,
                        "this stamp no longer reproduces the rounding; the "
                        "comment above needs rechecking, not deleting")

    def test_a_result_nobody_was_given_is_never_reaped(self):
        """The job finished while the client was away: that is exactly the
        result the retry is coming back for."""
        removed = self.queue.reap_delivered(ttl=0, now=float("inf"))
        self.assertEqual(removed, [])
        self.assertIn("J1", self.queue.jobs)

    def test_the_default_ttl_outlasts_the_clients_retry_budget(self):
        """The client retries for five minutes (JOB_STATUS_RETRY_BUDGET);
        every one of those retries must land while the result is kept."""
        self.assertGreaterEqual(Queue.DELIVERED_RESULT_TTL, 2 * 300)

    def test_the_reaper_runs_on_every_status_poll(self):
        from src import paintomicsserver
        body = _status_handler_body(paintomicsserver)
        self.assertIn("self.queue.reap_delivered()", body,
                      "nothing sweeps delivered results, so every one is kept "
                      "until the next step reuses its id")


class TheOldSemanticsStayForTheirCallersTest(unittest.TestCase):

    def test_get_result_still_consumes(self):
        """The input converter, "Choose for me" and the AI batch pick their
        result up once and rely on the pop."""
        queue = _queue()
        _add(queue, "J1", JobStatus.FINISHED, _Result())
        self.assertTrue(hasattr(queue.get_result("J1"), "getResponse"))
        self.assertEqual(queue.get_result("J1"), JobStatus.NOT_QUEUED)

    def test_enqueue_with_the_same_id_clears_a_delivered_run(self):
        """Step 2 enqueues under step 1's id; a kept result must not block it."""
        queue = Queue()
        queue.enqueue(fn=lambda: None, args=(), job_id="J1")
        queue.jobs["J1"].status = JobStatus.FINISHED
        queue.jobs["J1"].result = _Result()
        queue.deliver_result("J1")

        queue.enqueue(fn=lambda: None, args=(), job_id="J1")

        self.assertEqual(queue.jobs["J1"].status, JobStatus.QUEUED)
        self.assertIsNone(queue.jobs["J1"].delivered_at,
                          "the new run inherited the old run's delivery stamp")


def _status_handler_body(module):
    source = inspect.getsource(module.Application.__init__)
    marker = "def checkJobStatus"
    if marker not in source:
        raise AssertionError("checkJobStatus is not defined in Application.__init__")
    return source.split(marker, 1)[1].split("\n        def ", 1)[0]


class HandlerReadsWithoutConsumingTest(unittest.TestCase):

    def setUp(self):
        from src import paintomicsserver
        self.module = paintomicsserver
        self.body = _status_handler_body(paintomicsserver)

    def test_the_finished_branch_delivers(self):
        self.assertIn("self.queue.deliver_result(jobID)", self.body)

    def test_the_handler_never_consumes(self):
        self.assertNotIn("get_result(", self.body,
                         "the status handler consumes the result on delivery, "
                         "so a lost response still loses the job")

    def test_the_failed_branch_delivers_too(self):
        failed = self.body.split("is_failed()", 1)[1].split("else:", 1)[0]
        self.assertIn("deliver_result(jobID)", failed)
        self.assertIn("error_message", failed)

    def test_both_terminal_branches_carry_the_delivery_token(self):
        """The header the client hands back with its acknowledgement."""
        finished = self.body.split("is_finished()", 1)[1].split("is_failed()", 1)[0]
        failed = self.body.split("is_failed()", 1)[1].split("else:", 1)[0]
        self.assertIn("withDeliveryToken(", finished)
        self.assertIn("withDeliveryToken(", failed)
        source = inspect.getsource(self.module.Application.__init__)
        self.assertIn('DELIVERY_HEADER = "X-Paintomics-Delivery"', source)

    def test_the_ack_route_exists_and_demands_the_token(self):
        source = inspect.getsource(self.module.Application.__init__)
        self.assertIn("'/ack_job_result/<path:jobID>'", source)
        body = source.split("def acknowledgeJobResult", 1)[1].split("\n        def ", 1)[0]
        self.assertIn("self.queue.acknowledge(jobID, delivery)", body)
        self.assertIn('request.form.get("delivery")', body)


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
