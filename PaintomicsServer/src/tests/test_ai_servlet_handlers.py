#!/usr/bin/env python3
"""The AI interpretation routes, driven against a stand-in gateway.

The interpretation is the universal graph walk. These tests pin the contract
the four routes the browser already speaks keep around it:

  * /ai_interpret_initiate files the walk once: a second call reports the walk
    already queued or finished, a call after a failed walk files it again, and
    a newly filed walk forgets the conversation grounded in the one it replaces.
  * /ai_interpret_status reports the walk, and a walk still waiting for a free
    worker is not declared dead by the stale rule.
  * /ai_interpret_report returns the sealed walk and the pathways its legs run
    through, and says so plainly while the walk is still running.
  * /ai_interpret_chat refuses before the walk is sealed, and afterwards sends
    the gateway the walk and the design card, not an empty context.

The gateway stand-in is a real HTTP server speaking chat-completions, so
`llm_client` does real requests rather than being mocked out. The walk store,
the conversation store, the queue, the session and the job loader are in
memory: nothing here touches MongoDB.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_ai_servlet_handlers
"""
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.tests import walker_fixture as fx                                  # noqa: E402
from src.tests.test_walk_service_and_routes import FakeQueue, FakeWalkDAO   # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        _Handler.bodies.append(json.loads(self.rfile.read(length) or b"{}"))
        body = json.dumps({
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "Stub answer about the walk."}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Request:
    """The slice of a Flask request these handlers read."""

    def __init__(self, form=None, cookies=None):
        self.form = form or {}
        self.cookies = cookies or {}


class FakeChatDAO(object):
    conversations = {}
    cleared = []

    def find_by_job_id(self, job_id):
        return {"jobID": job_id, "conversation": list(FakeChatDAO.conversations.get(job_id, []))}

    def append_chat(self, job_id, role, content):
        FakeChatDAO.conversations.setdefault(job_id, []).append({"role": role, "content": content})

    def clear_chat(self, job_id):
        FakeChatDAO.cleared.append(job_id)
        FakeChatDAO.conversations.pop(job_id, None)

    def closeConnection(self):
        return True


def _view():
    return {"chain": [{"n": 1, "kind": "step", "from": "g:1", "to": "g:2", "from_label": "Aaa",
                       "to_label": "Bbb", "reading": "Bbb gene expression rises", "reason": "hot",
                       "edge": {"db": "KEGG", "pathway": "tst00001", "name": "Test pathway one",
                                "subtype": "activation", "sign": 1, "dir": "with"}},
                      {"n": 2, "kind": "step", "from": "g:2", "to": "mir:tst-miR-1", "from_label": "Bbb",
                       "to_label": "tst-miR-1", "reading": "miRNA-seq", "reason": "regulator",
                       "edge": {"db": "job", "pathway": "miRNA-seq", "name": "job:miRNA-seq",
                                "subtype": "miRNA target", "sign": -1, "dir": "against"}}],
            "results": {"title": "Results", "summary": "Aaa drives Bbb.",
                        "paragraphs": [{"from_statement": 1, "legs": [1], "text": "Bbb rose [1]."}]},
            "statements": [{"n": 1, "claim": "Aaa activates Bbb", "legs": [1]}],
            "papers": {"1": {"pmid": "123", "title": "Aaa and Bbb"}}}


class _RouteCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        from src.conf import serverconf
        cls._savedProvider = dict(serverconf.AI_PROVIDERS[serverconf.AI_LLM_PROVIDER])
        serverconf.AI_PROVIDERS[serverconf.AI_LLM_PROVIDER].update(
            {"api_base": "http://127.0.0.1:%d/v1" % cls.server.server_address[1],
             "api_key": "stub", "model": "stub-model", "fallback_models": ""})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        from src.conf import serverconf
        serverconf.AI_PROVIDERS[serverconf.AI_LLM_PROVIDER].update(cls._savedProvider)

    def setUp(self):
        import src.servlets.AIInterpretServlet as servlet
        self.servlet = servlet
        FakeWalkDAO.store, FakeChatDAO.conversations, FakeChatDAO.cleared = {}, {}, []
        _Handler.bodies = []
        self.consent = True
        test = self
        job = fx.make_job()
        job.getAIConsent = lambda: test.consent
        job.getJobID = lambda: "J1"
        self.job = job

        class _Sessions(object):
            def isValidUser(self, *args):
                return True

        class _Jobs(object):
            def loadJobInstance(self, jobID):
                return job

        self.saved = {name: getattr(servlet, name) for name in (
            "AIWalkDAO", "AIInterpretDAO", "_requireJobAccess", "_requireLLMCredentials",
            "UserSessionManager", "JobInformationManager", "AI_INTERPRETATION_ENABLED")}
        servlet.AIWalkDAO = FakeWalkDAO
        servlet.AIInterpretDAO = FakeChatDAO
        servlet._requireJobAccess = lambda jobID, userID: job
        servlet._requireLLMCredentials = lambda: None
        servlet.UserSessionManager = _Sessions
        servlet.JobInformationManager = _Jobs
        servlet.AI_INTERPRETATION_ENABLED = True
        self.queue = FakeQueue()

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.servlet, name, value)

    def _response(self):
        # Response.content is read directly rather than via getResponse(): that
        # calls jsonify(), which needs a Flask application context.
        from src.paintomicsserver import Response
        return Response()


class InitiateTest(_RouteCase):

    def initiate(self, user=None, **form):
        form.setdefault("jobID", "J1")
        cookies = {"userID": user} if user else {}
        return self.servlet.aiInterpretInitiate(_Request(form, cookies), self._response(), self.queue).content

    def test_the_walk_is_filed_once_and_the_old_chat_forgotten(self):
        FakeChatDAO.conversations["J1"] = [{"role": "user", "content": "about the old report"}]
        first = self.initiate()
        self.assertEqual(first["status"], "queued")
        fn, args, job_id, _ = self.queue.filed[0]
        self.assertIs(fn, self.servlet.walk_service.run_job)
        self.assertEqual(args[:2], ("J1", "network"))
        self.assertEqual(FakeChatDAO.cleared, ["J1"])
        self.assertEqual(self.initiate()["status"], "already_running")
        self.assertEqual(len(self.queue.filed), 1)

    def test_a_finished_walk_is_kept_and_a_failed_one_filed_again(self):
        FakeWalkDAO().save_progress("J1", "network", {"status": "done", "viewJSON": json.dumps(_view())})
        self.assertEqual(self.initiate()["status"], "already_finished")
        self.assertEqual(self.queue.filed, [])
        FakeWalkDAO().save_progress("J1", "network", {"status": "error", "detail": "gateway down"})
        self.assertEqual(self.initiate()["status"], "queued")

    def test_a_read_only_viewer_cannot_restart_the_interpretation(self):
        self.job.getReadOnly = lambda: True
        self.job.getUserID = lambda: "owner"
        FakeWalkDAO().save_progress("J1", "network", {"status": "done", "viewJSON": json.dumps(_view())})
        FakeChatDAO.conversations["J1"] = [{"role": "user", "content": "the owner's question"}]
        out = self.initiate(user="viewer", restart="true")
        self.assertFalse(out["success"])
        self.assertIn("read-only", out["message"])
        self.assertEqual(self.queue.filed, [])
        self.assertEqual(FakeChatDAO.cleared, [], "a viewer erased the owner's conversation")
        self.assertTrue(FakeWalkDAO.store[("J1", "network")]["viewJSON"])
        self.assertEqual(self.initiate(user="owner", restart="true")["status"], "queued")

    def test_no_consent_no_walk(self):
        self.consent = False
        out = self.initiate()
        self.assertFalse(out["success"])
        self.assertIn("Enable AI pathway interpretation", out["message"])
        self.assertEqual(self.queue.filed, [])


class StatusTest(_RouteCase):

    def status(self):
        return self.servlet.aiInterpretStatus(_Request({"jobID": "J1"}), self._response(), self.queue).content

    def test_a_job_with_no_walk_is_not_started(self):
        self.assertEqual(self.status()["status"], "not_started")

    def test_a_walk_waiting_for_a_worker_is_not_declared_dead(self):
        from src.common.PySiQ import JobStatus
        from src.tests.test_walk_service_and_routes import FakeQueueJob
        FakeWalkDAO().save_progress("J1", "network", {"status": "queued", "percent": 0})
        FakeWalkDAO.store[("J1", "network")]["updatedAt"] = datetime.utcnow() - timedelta(minutes=30)
        self.queue.jobs[self.servlet.walk_service.queue_id("J1", "network")] = FakeQueueJob(JobStatus.QUEUED)
        self.assertEqual(self.status()["status"], "queued")
        self.queue.jobs.clear()
        self.assertEqual(self.status()["status"], "error", "a queued walk the queue no longer holds is dead")


class ReportTest(_RouteCase):

    def report(self):
        return self.servlet.aiInterpretReport(_Request({"jobID": "J1"}), self._response()).content

    def test_the_report_waits_for_the_walk(self):
        self.assertFalse(self.report()["success"])
        FakeWalkDAO().save_progress("J1", "network", {"status": "running", "percent": 40})
        out = self.report()
        self.assertFalse(out["success"])
        self.assertIn("in progress", out["message"])

    def test_a_sealed_walk_is_the_report(self):
        FakeWalkDAO().save_progress("J1", "network", {"status": "done", "viewJSON": json.dumps(_view())})
        out = self.report()
        self.assertTrue(out["success"])
        self.assertEqual(out["walk"]["results"]["summary"], "Aaa drives Bbb.")
        self.assertEqual(out["pathways"], [{"id": "tst00001", "name": "Test pathway one", "source": "KEGG"}],
                         "a leg on the job's own miRNA edge is not a pathway")
        self.job.getMatchedPathways = lambda: {"tst00002": object()}
        self.assertEqual(self.report()["pathways"], [],
                         "a pathway the job never analysed has no diagram for a chat link to open")


class ChatHandlerTest(_RouteCase):

    def _call(self, **form):
        return self.servlet.aiInterpretChat(_Request(form=form), self._response()).content

    def test_an_empty_message_is_refused(self):
        result = self._call(jobID="J1", message="")
        self.assertFalse(result.get("success"))
        self.assertIn("mpty", str(result.get("message", "")))

    def test_a_missing_job_id_is_refused(self):
        result = self._call(message="what does this mean?")
        self.assertFalse(result.get("success"))
        self.assertIn("jobID", str(result.get("message", "")))

    def test_chatting_before_the_walk_is_sealed_is_refused(self):
        result = self._call(jobID="J1", message="summarise this")
        self.assertFalse(result.get("success"))
        self.assertIn("finished", str(result.get("message", "")))
        self.assertEqual(_Handler.bodies, [], "the gateway was called with nothing to ground the answer")

    def test_the_chat_is_grounded_in_the_walk_and_the_design_card(self):
        FakeWalkDAO().save_progress("J1", "network", {"status": "done", "viewJSON": json.dumps(_view())})
        result = self._call(jobID="J1", message="What drives Bbb?")
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result["response"], "Stub answer about the walk.")
        sent = json.dumps(_Handler.bodies[0]["messages"])
        self.assertIn("Aaa drives Bbb.", sent, "the walk's Results never reached the gateway")
        self.assertIn("Design card:", sent)
        self.assertIn("get_feature_values", json.dumps(_Handler.bodies[0].get("tools")))
        self.assertEqual([m["role"] for m in FakeChatDAO.conversations["J1"]], ["user", "assistant"])


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
