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

A request waits at most WAIT_LIMIT seconds. Both kinds run on the queue, and a
waiting request holds one of its N_WORKERS (4) workers while it waits, so an
unbounded wait behind a request that never ends (an R script that hung, before
generateMetagenesList had a time limit) could take every worker, one slider
move at a time, and stop every other job on the server. A request that has
waited its limit is not run: it raises JobBusyError, whose message says the job
is still busy with another change and to try again in a moment, and the browser
shows that as a warning, not as an error to report.
"""

import logging
import threading
from contextlib import contextmanager

from src.common.ServerErrorManager import JobBusyError, quotable

# How long a request waits for the one ahead of it on the same job, in
# seconds, and so the longest it holds a queue worker doing nothing. In
# paintomics.org's logs from 15 Sep to 9 Nov 2026 the two slider requests took
# 3 and 5 s, and Step 2's metagenes phase took 4 s at the median and 32 s at
# most over 215 runs: a request waits through about a dozen slider moves
# before it gives up. A slider move made while a whole Step 2 of the job runs
# (Step 2 takes a minute or more with metabolites) can give up; it says so.
WAIT_LIMIT = 60


class _Entry(object):
    """One job's lock, and who uses it."""

    def __init__(self):
        self.lock = threading.RLock()
        # How many requests hold the lock or wait for it. The entry goes when
        # the last of them is done, so the table holds only the jobs being
        # rewritten now, not every job the server has seen.
        self.users = 0
        # What the request holding the lock is doing, for the message of one
        # that gives up waiting ("making new clusters"), and how many times
        # that request holds it (the lock is re-entrant).
        self.doing = None
        self.depth = 0


# jobID -> _Entry
_locks = {}
_tableLock = threading.Lock()


def _forget(key, entry):
    """One user fewer; the entry goes with the last."""
    with _tableLock:
        entry.users -= 1
        if entry.users == 0:
            del _locks[key]


def busyMessage(jobID, doing, waited):
    """What a request that gave up waiting answers. The job id is repeated only
    when it is a plain id (see quotable); `waited` is in seconds."""
    shown = quotable(jobID)
    return ("%s is still %s, and this request waited %s for it to finish. Nothing was "
            "changed. Please try again in a moment."
            % ("Job " + shown if shown else "This job",
               "busy %s for an earlier request" % doing if doing else "busy with an earlier request",
               _seconds(waited)))


def _seconds(value):
    return "%g second%s" % (value, "" if value == 1 else "s")


def acquire(jobID, doing=None, waitLimit=None):
    """Wait until no other request rewrites this job, then hold it.

    `doing` says what this request does to the job, as the message of a
    request that gives up waiting for it puts it: "making new clusters",
    "running Step 2". Waits at most `waitLimit` seconds (WAIT_LIMIT when not
    given) and then raises JobBusyError, holding nothing.

    Every acquire that returns needs its release(jobID), from the same
    thread, in a `finally`; heldFor does both. The lock is re-entrant: a
    request already holding it goes on rather than waiting for itself.
    """
    key = str(jobID)
    with _tableLock:
        entry = _locks.get(key)
        if entry is None:
            entry = _locks[key] = _Entry()
        entry.users += 1
    if not entry.lock.acquire(blocking=False):
        # The one line an operator needs to tell a slow request from a stuck
        # one: it was waiting, and for what.
        logging.info("JOB %s - waiting for another request that is changing this job (%s)",
                     key, entry.doing or "unknown")
        limit = WAIT_LIMIT if waitLimit is None else waitLimit
        if not entry.lock.acquire(timeout=limit):
            # Read before letting go of the entry: the holder may finish now.
            holderDoing = entry.doing
            _forget(key, entry)
            logging.warning("JOB %s - gave up after waiting %s s for another request (%s); "
                            "this one (%s) was not run", key, limit, holderDoing or "unknown",
                            doing or "unknown")
            raise JobBusyError(busyMessage(jobID, holderDoing, limit))
    if entry.depth == 0:
        entry.doing = doing
    entry.depth += 1


def release(jobID):
    """Let the next request for this job go on (see acquire)."""
    key = str(jobID)
    with _tableLock:
        entry = _locks[key]
    entry.depth -= 1
    if entry.depth == 0:
        entry.doing = None
    entry.lock.release()
    _forget(key, entry)


@contextmanager
def heldFor(jobID, doing=None, waitLimit=None):
    """Run the `with` block while no other request rewrites this job; raise
    JobBusyError, without running it, when the job stays busy (see acquire)."""
    acquire(jobID, doing, waitLimit)
    try:
        yield
    finally:
        release(jobID)


def jobsHeld():
    """The ids of the jobs some request holds or waits for (for tests)."""
    with _tableLock:
        return sorted(_locks)
