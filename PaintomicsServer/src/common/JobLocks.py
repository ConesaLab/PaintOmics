#***************************************************************
#  This file is part of Paintomics v4
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
#**************************************************************
"""One request at a time rewrites a job's pathways, per job.

Step 2 and the Step 3 cluster slider ("Generate N clusters", then Apply) both
rewrite the same three things of one job:

  * the metagenes of its pathways, on the PathwayAcquisitionJob object that
    every request for the job shares (JobInformationManager.loadJobInstance
    hands them all the cached one);
  * the cluster pictures in its output folder (replaceMetagenePictures);
  * its pathway documents in MongoDB, which storePathways and step 2's store
    remove and insert again, in two calls with nothing around them.

Both run on the queue, whose N_WORKERS threads (4) share one process. The
queue already keeps one Step 2 per job: it is queued under the job's own id
and a second one is refused (test_one_step_per_job_at_a_time). The slider is
queued under a new id for every request, so two moves of the slider on one job,
or a move and a Step 2, ran at the same time. Each slider request also worked
in the job's one temporary folder and removed it when it finished, under the
other's R script.

They now share one rule (heldFor in the slider, acquire and release in Step
2): a request for a job waits for the one already rewriting it, and requests
for other jobs never wait on it.

The lock lives in the process, which is enough because there is one process:
uWSGI runs `processes = 1` (deploy/uwsgi.ini, paintomics4.ini), as it must for
the queue, which lives in that process's memory too (see JobProgress).
"""

import logging
import threading
from contextlib import contextmanager

# jobID -> [lock, how many requests hold it or wait for it]. An entry goes
# when the last of them is done, so the table holds only the jobs being
# rewritten now, not every job the server has seen.
_locks = {}
_tableLock = threading.Lock()


def acquire(jobID):
    """Wait until no other request rewrites this job, then hold it.

    Every acquire needs its release(jobID), from the same thread, in a
    `finally`; heldFor does both. The lock is re-entrant: a request already
    holding it goes on rather than waiting for itself.
    """
    key = str(jobID)
    with _tableLock:
        entry = _locks.get(key)
        if entry is None:
            entry = _locks[key] = [threading.RLock(), 0]
        entry[1] += 1
    lock = entry[0]
    if not lock.acquire(blocking=False):
        # The one line an operator needs to tell a slow request from a stuck
        # one: it was waiting, and for what.
        logging.info("JOB %s - waiting for another request that is changing this job", key)
        lock.acquire()


def release(jobID):
    """Let the next request for this job go on (see acquire)."""
    key = str(jobID)
    with _tableLock:
        entry = _locks[key]
    entry[0].release()
    with _tableLock:
        entry[1] -= 1
        if entry[1] == 0:
            del _locks[key]


@contextmanager
def heldFor(jobID):
    """Run the `with` block while no other request rewrites this job."""
    acquire(jobID)
    try:
        yield
    finally:
        release(jobID)


def jobsHeld():
    """The ids of the jobs some request holds or waits for (for tests)."""
    with _tableLock:
        return sorted(_locks)
