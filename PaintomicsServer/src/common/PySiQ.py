#***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#
# THIS FILE CONTAINS THE FOLLOWING COMPONENT DECLARATION
#  - JobStatus
#  - WorkerStatus
#  - Queue
#  - Worker
#  - WorkerThread
#  - Job
#  -
#**************************************************************

#TODO: TIMEOUT
#TODO: AUTO REMOVE JOBS

import logging
import time
from threading import RLock as threading_lock, Thread
from collections import deque
from enum import Enum

class JobStatus(Enum):
    QUEUED  ='queued'
    FINISHED='finished'
    FAILED  ='failed'
    STARTED ='started'
    DEFERRED='deferred'
    NOT_QUEUED='not queued'

class WorkerStatus(Enum):
    WORKING ='working'
    IDLE    ='idle'
    STOPPED ='stopped'

class Queue:
    def __init__(self):
        logging.info("CREATING NEW INSTANCE FOR Queue...")
        self.lock = threading_lock()
        self.queue= deque([])
        self.jobs = {}
        self.workers = []

    def start_worker(self, n_workers=1):
        ids = []
        worker_id=""
        for i in range(0, n_workers):
            worker_id = "w" + self.get_random_id()
            self.workers.append(Worker(worker_id, self))
            ids.append(worker_id)
        return ids

    def stop_worker(self, worker_id=None):
        try:
            self.lock.acquire() #LOCK CACHE
            if worker_id == None:
                for worker in self.workers:
                    if worker.must_die != True:
                        worker_id = worker.id
                        break
                if worker_id == None:
                    logging.info("All workers will die...")

            for worker in self.workers:
                if worker.id == worker_id:
                    worker.must_die = True
                    break
        finally:
            self.lock.release() #UNLOCK CACHE
            self.notify_workers()

    def remove_worker(self, worker_id):
        try:
            self.lock.acquire() #LOCK CACHE
            i=0
            for worker in self.workers:
                if worker.id == worker_id and worker.must_die == True:
                    self.workers.pop(i)
                    break
                i+=1
        finally:
            self.lock.release() #UNLOCK CACHE
            self.notify_workers()

    def enqueue(self, fn, args, job_id="", timeout=600):
        try:
            self.lock.acquire() #LOCK CACHE
            job = Job(fn, args)

            if job_id=="":
                job_id = self.get_random_id()
                while job_id in self.jobs:
                    job_id = self.get_random_id()
            elif job_id in self.jobs:
                # A finished run is cleared so the next step can be submitted
                # under the same id -- step 1 and step 2 both enqueue as
                # `jobID`, so without this a job could never advance.
                #
                # FAILED belongs here for the same reason: that run is over too.
                # It used to fall to the else, and since nothing else removes a
                # failed entry, the id stayed blocked until the server was
                # restarted. The one route that clears it is the status poll
                # (`is_failed()` -> `get_result`), so the entry survives exactly
                # when the client stops polling before seeing the failure -- a
                # closed tab, a dropped connection. Every retry then got
                #     RuntimeError: Job already at the queue
                # which is not true: nothing is queued, the corpse is. The user
                # is told to wait for a job that will never run, and the only
                # way out is to resubmit the data as a new job.
                if self.jobs.get(job_id).status in (JobStatus.FINISHED,
                                                    JobStatus.FAILED):
                    self.get_result(job_id)
                else:
                    raise RuntimeError("Job already at the queue (Job id : " + job_id + ")")

            job.set_id(job_id)
            job.set_timeout(timeout)

            self.jobs[job_id] = job
            self.queue.append(job)

            logging.info("NEW JOB "  + job_id + " ADDED TO QUEUE...")
        finally:
            self.lock.release() #UNLOCK CACHE
            self.notify_workers()

    def dequeue(self):
        try:
            self.lock.acquire() #LOCK CACHE
            if len(self.queue) > 0:
                return self.queue.popleft()
            return None
        finally:
            self.lock.release() #UNLOCK CACHE

    def notify_workers(self):
        for worker in self.workers:
            worker.notify()

    def check_status(self, job_id):
        job = self.jobs.get(job_id, None)
        if job:
            return job.status
        return JobStatus.NOT_QUEUED

    def fetch_job(self, job_id):
        return self.jobs.get(job_id, None)

    def get_result(self, job_id, remove=True):
        """Return a job's result, consuming it if it has finished.

        The lookup and the removal run under the queue lock. They used to be a
        bare check-then-act on a shared dict, so two callers arriving together
        could both find the job and both try to take it; the loser got the
        JobStatus enum back, which the status handler then called getResponse()
        on. Locking makes "who gets the result" a decision rather than a race,
        and exactly one caller wins.

        The status poll no longer takes results through here: see
        deliver_result. This stays for the one-shot pickups (the input
        converter, "Choose for me", the AI batch) and for enqueue(), which
        clears a finished run so the next step can reuse the id.
        """
        try:
            self.lock.acquire() #LOCK CACHE
            job = self.jobs.get(job_id, None)
            if job:
                if remove and (job.status == JobStatus.FINISHED or job.status == JobStatus.FAILED):
                    logging.info("Removing job " + job_id)
                    self.jobs.pop(job_id, None)
                return job.result
            return JobStatus.NOT_QUEUED
        finally:
            self.lock.release() #UNLOCK CACHE

    # How long a delivered result stays collectable after the first time it was
    # handed out. The client acknowledges a result it has received, which
    # removes the entry at once; this is the ceiling for a client that never
    # does -- a closed tab, a browser that received the answer and crashed.
    # Ten minutes is twice the client's retry budget (JOB_STATUS_RETRY_BUDGET
    # in ServerConfiguration.js), so every retry the client will ever make
    # lands while the result is still there.
    DELIVERED_RESULT_TTL = 600

    def deliver_result(self, job_id):
        """Hand out a finished (or failed) job's result WITHOUT consuming it.

        Why this exists. get_result pops the entry the first time it is read,
        so the one HTTP response carrying the result was the only copy that
        would ever exist. On 2026-09-11 a status poll for a finishing job on
        paintomics.uv.es took 61 s (RAM pressure), nginx answered it with a
        504 after its default 60 s, and the job then finished and was stored
        at 13:09:42 with nobody polling; the user came back five hours later
        and redid every step. A client that retries such a poll is only half
        the fix -- had the lost response been the one carrying the result,
        the retry would have been told "Your job is not on the queue anymore".

        So the first delivery stamps `delivered_at` and leaves the entry in
        place. It is removed by acknowledge() when the client confirms it has
        the result, by reap_delivered() once DELIVERED_RESULT_TTL has passed,
        or by enqueue() when the next step reuses the id. A second poller that
        arrives before any of those gets the same result, which is the honest
        answer: the job did finish, and both tabs are drawing the same job.

        Returns the job's result for a FINISHED or FAILED job (for a failed
        job that is whatever the worker stored, usually None; read the error
        off the job itself), the JobStatus enum for one still running, and
        JobStatus.NOT_QUEUED for one that is not here.
        """
        try:
            self.lock.acquire() #LOCK CACHE
            job = self.jobs.get(job_id, None)
            if job is None:
                return JobStatus.NOT_QUEUED
            if job.status not in (JobStatus.FINISHED, JobStatus.FAILED):
                return job.status
            if job.delivered_at is None:
                job.delivered_at = time.monotonic()
                logging.info("Delivering result of job " + job_id)
            return job.result
        finally:
            self.lock.release() #UNLOCK CACHE

    def acknowledge(self, job_id):
        """The client has the result: drop the entry. True when one was dropped.

        Only a finished or failed job is dropped; acknowledging a running job
        (a stale request, a wrong id) changes nothing.
        """
        try:
            self.lock.acquire() #LOCK CACHE
            job = self.jobs.get(job_id, None)
            if job is None or job.status not in (JobStatus.FINISHED, JobStatus.FAILED):
                return False
            logging.info("Job " + job_id + " acknowledged, removing it")
            self.jobs.pop(job_id, None)
            return True
        finally:
            self.lock.release() #UNLOCK CACHE

    def reap_delivered(self, ttl=None, now=None):
        """Drop every delivered result older than `ttl` seconds.

        Called from the status route rather than from a timer thread: the
        jobs dict holds a handful of entries, so the scan costs nothing, and
        a sweep that only happens while someone is polling cannot be the
        thread that dies unnoticed. Returns the ids it removed.
        """
        if ttl is None:
            ttl = self.DELIVERED_RESULT_TTL
        if now is None:
            now = time.monotonic()
        try:
            self.lock.acquire() #LOCK CACHE
            expired = [job_id for job_id, job in self.jobs.items()
                       if job.delivered_at is not None
                       and job.status in (JobStatus.FINISHED, JobStatus.FAILED)
                       and now - job.delivered_at >= ttl]
            for job_id in expired:
                logging.info("Result of job " + job_id + " was delivered "
                             + str(ttl) + "s ago and never acknowledged, removing it")
                self.jobs.pop(job_id, None)
            return expired
        finally:
            self.lock.release() #UNLOCK CACHE

    def get_error_message(self, job_id):
        job = self.jobs.get(job_id, None)
        if job:
            return job.error_message
        return None

    def get_random_id(self):
        """
        This function returns a new random job id
        @returns jobID
        """
        #RANDOM GENERATION OF THE JOB ID
        #TODO: CHECK IF NOT EXISTING ID
        import string, random
        jobID = ''.join(random.sample(string.ascii_letters+string.octdigits*5,10))
        return jobID

class Worker():
    def __init__(self, _id, _queue):
        self.id = _id
        self.queue = _queue
        self.status = WorkerStatus.IDLE
        self.must_die = False
        self.job = None
        # Guards the claim sequence in notify(). See there for why.
        self.lock = threading_lock()

    def notify(self):
        """Take one job, if this worker is free. Safe to call concurrently.

        This used to check the status, dequeue, assign self.job and start a
        thread without holding anything. Queue.dequeue() takes the queue lock,
        but the sequence around it was not atomic and self.job is a single slot
        on a shared Worker -- and Queue.enqueue() calls notify_workers() after
        releasing its own lock, so two submissions arriving together produce
        two concurrent notify() calls on the same worker.

        Both then saw a non-WORKING status, both dequeued -- taking two
        different jobs -- and the second assignment to self.job discarded the
        first. Observed on the server by submitting two example jobs at once:
        one was taken off the deque and never executed, still reporting QUEUED
        three minutes later with no error, while the other was started twice
        and had two threads writing the same output directory ("Failed while
        compressing directory", then AttributeError: 'NoneType' object has no
        attribute 'result' as one thread's finally cleared self.job under the
        other).

        The status is claimed inside the lock and *before* the thread starts:
        setting it in run() left exactly the window a second notify() needed.
        self.job is checked too, so a worker holding a job cannot be handed
        another even if its status has not been updated yet.
        """
        self.lock.acquire()
        try:
            if self.status == WorkerStatus.WORKING or self.job is not None:
                return
            if self.must_die:
                mustRemove = True
            else:
                job = self.queue.dequeue()
                if job is None:
                    return
                self.job = job
                self.status = WorkerStatus.WORKING
                WorkerThread(self).start()
                return
        finally:
            self.lock.release()

        # remove_worker() calls back into notify_workers(), so it must not run
        # while this worker's lock is held.
        if mustRemove:
            self.queue.remove_worker(self.id)

    def run(self):
        # Taken once, under the lock, rather than read from self.job
        # throughout: notify() owns that slot, and reading it repeatedly is
        # what let one thread's finally clear it while another was still using
        # it ("AttributeError: 'NoneType' object has no attribute 'result'").
        self.lock.acquire()
        try:
            job = self.job
        finally:
            self.lock.release()

        if job is None:                      # nothing claimed; nothing to do
            return

        try:
            logging.info("Worker " + self.id + " starts working...")
            #Execute the function
            fn = job.fn
            args = job.args
            # The moment work actually begins — everything before this was queue
            # wait, which the user should see as "waiting", not as slow progress.
            job.started_at = time.monotonic()
            job.status = JobStatus.STARTED
            job.result = fn(*args)
            job.status = JobStatus.FINISHED
        except Exception as ex:
            job.status = JobStatus.FAILED
            job.error_message = str(ex)
        finally:
            logging.info("Worker " + self.id + " stops working...")
            # Released under the lock so it cannot cross a concurrent claim in
            # notify(); the follow-up notify() is called after releasing, so a
            # long dequeue never blocks another submission.
            self.lock.acquire()
            try:
                self.status = WorkerStatus.IDLE
                self.job = None
            finally:
                self.lock.release()
            self.notify()

class WorkerThread (Thread):
    def __init__(self, worker):
        Thread.__init__(self)
        self.worker = worker
    def run(self):
        self.worker.run()

class Job:
    def __init__(self, fn, args):
        self.fn = fn
        self.args = args
        self.id = None
        self.timeout = 600
        self.status = JobStatus.QUEUED
        self.result = None
        self.error_message=None
        # time.monotonic(), not time.time(): these are only ever subtracted from
        # each other, and a wall clock can step (NTP) and produce a negative
        # duration. queued_at is set here because Job() is constructed inside
        # enqueue(); started_at is set by the worker that claims it.
        #
        # Without these, "how long has this job been running" was measured from
        # Job.startTime in the *domain* object, which is stamped in the web
        # request before the files are even saved — so upload and queue wait
        # were silently billed as job runtime.
        self.queued_at = time.monotonic()
        self.started_at = None
        # When the result was first handed to a client (deliver_result). None
        # until then; the reaper only looks at entries that carry a stamp.
        self.delivered_at = None

    def set_id(self, _id):
        self.id = _id
    def set_timeout(self, _timeout):
        self.timeout = _timeout

    def is_finished(self):
        return  self.status == JobStatus.FINISHED
    def is_failed(self):
        return self.status == JobStatus.FAILED
    def get_status(self):
        return self.status