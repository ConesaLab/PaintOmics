#!/usr/bin/env python3
"""The status endpoint must return what the walker has been doing.

The interpretation is a graph walk, and the walk stores its chain as it grows
(aiWalkCollection, liveJSON). A progress bar alone would show a user a
percentage and a sentence for minutes; the legs walked so far, newest last,
are what the panel's activity feed shows, and the total is reported so a
trimmed feed never looks like a lost one.

    python -m src.tests.test_status_returns_tool_trace
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import src.servlets.AIInterpretServlet as S            # noqa: E402
from src.common.DAO.AIWalkDAO import AIWalkDAO           # noqa: E402

_PASSED, _FAILED = [], []
JOB = "TRACEPROBE_TEST"


class _Session:
    def isValidUser(self, *a, **kw):
        return True


class _Job:
    """The job `JOB` stands for, as far as the status route is concerned.

    The fixture used to seed only an `aiInterpretationCollection` record and no
    `jobInstanceCollection` document, which was fine while the route read the
    progress record and nothing else. It now asks who owns the job first, and a
    job that will not load is refused -- deliberately, and in the manner
    `_consented()` already established: treating an unknown job as permission is
    the wrong way round for a question about someone else's data.

    So the loader is stubbed here exactly as the session is, and for the same
    reason. This suite is about whether the tool trace reaches the client; it
    should not also have to construct a real PathwayAcquisitionJob, and it must
    not be the thing that notices if the ownership check is ever removed --
    test_ai_routes_check_job_ownership owns that question.

    An unowned job (userID None) is the right stand-in: those are the guest jobs
    the application supports, and they are what an anonymous caller may read.
    """

    def getUserID(self):
        return None

    def getAllowSharing(self):
        return False


class _Request:
    cookies = {"userID": "u", "sessionToken": "t"}
    form = {"jobID": JOB}


class _Response:
    def __init__(self):
        self.content = None

    def setContent(self, content):
        self.content = content


def _leg(n, kind="step"):
    return {"n": n, "kind": kind, "from": "g:%d" % n, "to": "g:%d" % (n + 1),
            "from_label": "Gene%d" % n, "to_label": "Gene%d" % (n + 1), "reading": "r", "reason": "why",
            "edge": None if kind == "jump" else {"db": "KEGG", "pathway": "mmu04068", "name": "FoxO",
                                                  "subtype": "activation", "sign": 1, "dir": "with"}}


def _seed(legs, status="running"):
    import json
    dao = AIWalkDAO()
    try:
        field = "liveJSON" if status == "running" else "viewJSON"
        dao.save_progress(JOB, "network", {"status": status, "stage": "walk", "percent": 40,
                                           "detail": "Walking", field: json.dumps({"chain": legs})})
    finally:
        dao.closeConnection()


def _cleanup():
    dao = AIWalkDAO()
    try:
        dao.dbManager.getCollection(dao.collectionName).delete_many({"jobID": JOB})
    finally:
        dao.closeConnection()


class _Loader:
    def loadJobInstance(self, jobID):
        return _Job()


def _status():
    originalSession = S.UserSessionManager
    originalJobs = S.JobInformationManager
    S.UserSessionManager = lambda: _Session()
    S.JobInformationManager = lambda: _Loader()
    try:
        response = _Response()
        S.aiInterpretStatus(_Request(), response)
        return response.content or {}
    finally:
        S.UserSessionManager = originalSession
        S.JobInformationManager = originalJobs


def test_the_trace_reaches_the_client():
    _seed([_leg(1), _leg(2, "jump"), _leg(3)])
    try:
        body = _status()
        assert "toolTrace" in body, ("the status payload has no toolTrace: %s"
                                     % sorted(body))
        assert len(body["toolTrace"]) == 3
        assert body["toolTrace"][0]["tool"] == "step"
        assert body["toolTrace"][1]["tool"] == "jump"
        assert body["toolTrace"][0]["args"] == "Gene1 → Gene2", body["toolTrace"][0]
        assert body.get("toolCalls") == 3, "the total leg count is missing"
    finally:
        _cleanup()


def test_a_long_run_is_trimmed_to_the_tail():
    """A ten-minute run makes hundreds of calls; the widget shows a short feed,
    and the total is reported separately so nothing looks lost."""
    _seed([_leg(n) for n in range(1, 41)])
    try:
        body = _status()
        assert len(body["toolTrace"]) == 12, (
            "expected the last 12 legs, got %d" % len(body["toolTrace"]))
        assert body["toolTrace"][-1]["args"] == "Gene40 → Gene41", "not the tail"
        assert body["toolCalls"] == 40, "the total should count every leg"
    finally:
        _cleanup()


def test_a_finished_walk_still_shows_its_legs():
    """Once sealed the chain lives in the view, not the live record."""
    _seed([_leg(1), _leg(2)], status="done")
    try:
        body = _status()
        assert body["status"] == "done"
        assert body["toolCalls"] == 2
    finally:
        _cleanup()


def test_no_walk_is_not_started():
    _cleanup()
    body = _status()
    assert body["status"] == "not_started", body


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_the_trace_reaches_the_client,
              test_a_long_run_is_trimmed_to_the_tail,
              test_a_finished_walk_still_shows_its_legs,
              test_no_walk_is_not_started):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
