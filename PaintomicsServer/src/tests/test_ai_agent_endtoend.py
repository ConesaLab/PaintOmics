#!/usr/bin/env python3
"""The AI agent workflow, run end to end against a local stand-in gateway.

Why this exists
---------------
Nothing ran `run_ai_agent`. It needs a live LLM, and a deployment without
`AI_CSIC_API_KEY` cannot reach one -- so phase sequencing, the reference
rendering, and the citation verification loop were all covered only by unit
tests of their parts.

That gap hid a real defect once already. `docs/superpowers/specs/` records the
synthesis emitting no parseable `### References` section in three runs of four,
which made `verify_report` check nothing, report `citations_checked: 0` and
`ref_accuracy: 0.0`, and finish `done` regardless. The fix was to stop asking
the model for the section and render it from `paper_index`. This is the test
that says so.

The gateway stand-in is not a mock of the client: it is an HTTP server speaking
chat-completions, so `llm_client` does real requests, real JSON handling and its
real schema path. When the caller asks for a `json_schema` response it builds a
minimal instance of that schema; otherwise it returns prose carrying `[1]`
markers, which is what gives `render_references_section` something to resolve.

PubMed is **not** stubbed. The bibliographic half of a reference has to come
from a real lookup for this test to mean anything -- the whole point is that the
metadata is ground truth and only the quotation comes from the model.

Skips cleanly when MongoDB, a stored job, or the network is unavailable, in the
manner of test_enrichment_e2e and test_more_backend_endtoend.

**This writes to the real database, unlike the rest of the suite.**
`test_pymongo4_compat` and `test_user_identity_security` each build a scratch
database and drop it afterwards; that is the better pattern and the one to copy
where it is possible. It is not possible here -- the pipeline loads a real job
through `loadJobInstance`, and cloning one means cloning it across
jobInstanceCollection, featuresCollection, pathwaysCollection and
foundFeaturesCollection. So the stored interpretation is captured, restored, and
the restore is *verified*. That is weaker than isolation, and is said plainly
rather than glossed.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_ai_agent_endtoend [--jobID XXXX]
"""
import argparse
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import tempfile  # noqa: E402

# Redirect the trace archive before anything runs a job.
#
# This suite drives run_ai_agent, which used to dispatch to the six-phase
# workflow arm -- and that arm never archived a trace, so this file had no
# reason to redirect. Removing the workflow made run_ai_agent dispatch
# unconditionally to the interpreter loop, which writes one JSONL per run into
# <CLIENT_TMP_DIR>/ai_traces. A stub run promptly landed in the live
# measurement corpus that every round in docs/ai-agent-benchmark.md is scored
# from, and only test_the_live_archive_holds_no_stub_runs caught it.
#
# Setting PAINTOMICS_CLIENT_TMP does NOT work: serverconf hardcodes
# CLIENT_TMP_DIR. _archive_trace imports the name inside the function, so
# rebinding the module attribute is what takes effect.
import src.conf.serverconf as _serverconf  # noqa: E402
_serverconf.CLIENT_TMP_DIR = tempfile.mkdtemp(prefix="stub_e2e_base_")

JOB_ID = os.environ.get("AI_E2E_JOB_ID")


# ---------------------------------------------------------------------------
# The stand-in gateway
# ---------------------------------------------------------------------------

def _instanceFor(schema, root=None, depth=0):
    """A minimal value satisfying `schema`, so the schema path runs for real.

    The Agents SDK derives its output schemas from pydantic models, which
    emit ``$defs`` + ``$ref`` and ``anyOf`` for Optional fields; a stub that
    cannot follow those hands the workflow a string where it required an
    object, and the run dies in triage.
    """
    if not isinstance(schema, dict):
        return None
    root = root if root is not None else schema
    if depth > 12:  # recursive models: bottom out rather than loop forever
        return None
    if "$ref" in schema:
        ref = schema["$ref"]
        target = root
        for part in ref.lstrip("#/").split("/"):
            target = (target or {}).get(part, {})
        return _instanceFor(target, root, depth + 1)
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            # Prefer the non-null branch so required content is populated.
            options = [o for o in schema[key]
                       if not (isinstance(o, dict) and o.get("type") == "null")]
            return _instanceFor((options or schema[key])[0], root, depth + 1)
    if schema.get("allOf"):
        merged = {}
        for part in schema["allOf"]:
            merged.update(part if isinstance(part, dict) else {})
        return _instanceFor(merged, root, depth + 1)
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0])
    if kind == "object":
        properties = schema.get("properties") or {}
        required = schema.get("required") or list(properties.keys())
        return {name: _instanceFor(properties.get(name, {}), root, depth + 1)
                for name in required}
    if kind == "array":
        return [_instanceFor(schema.get("items") or {}, root, depth + 1)]
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    return "stub"


SYNTHESIS = ("## Summary\n\nCoordinated regulation is visible across the "
             "submitted layers [1].\n\n## Caveats\n\nStub output; no biological "
             "meaning.\n")


class _Handler(BaseHTTPRequestHandler):
    served = []
    streamed = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        _Handler.served.append(payload)

        schema = ((payload.get("response_format") or {})
                  .get("json_schema") or {}).get("schema")
        content = json.dumps(_instanceFor(schema)) if schema else SYNTHESIS

        if payload.get("stream"):
            # The SDK transport streams every call and folds the chunks back
            # (agent._stream_to_completion), so the stand-in must speak SSE
            # the way vLLM does: role first, content in pieces, a closing
            # finish_reason, usage last, then [DONE].
            _Handler.streamed += 1
            half = max(1, len(content) // 2)
            chunks = [
                {"delta": {"role": "assistant", "content": ""}},
                {"delta": {"content": content[:half]}},
                {"delta": {"content": content[half:]}},
                {"delta": {}, "finish_reason": "stop"},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for i, c in enumerate(chunks):
                event = {"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                         "created": 1, "model": "stub-model",
                         "choices": [dict({"index": 0, "finish_reason": None}, **c)]}
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            if (payload.get("stream_options") or {}).get("include_usage"):
                usage = {"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                         "created": 1, "model": "stub-model", "choices": [],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                   "total_tokens": 2}}
                self.wfile.write(("data: " + json.dumps(usage) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        body = json.dumps({
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _startGateway():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d/v1" % server.server_address[1]


# ---------------------------------------------------------------------------

def _storedJobID():
    """A completed job to interpret; skip if the database has none."""
    if JOB_ID:
        return JOB_ID
    try:
        from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
        from pymongo import MongoClient
        client = MongoClient(MONGODB_HOST, int(MONGODB_PORT),
                             serverSelectionTimeoutMS=2000)
        collection = client["PaintomicsDB"]["jobInstanceCollection"]
        document = collection.find_one({"lastStep": {"$gte": 3}}, {"jobID": 1})
        return document.get("jobID") if document else None
    except Exception:
        return None


class AiPipelineEndToEndTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.jobID = _storedJobID()
        if not cls.jobID:
            raise unittest.SkipTest(
                "no completed job in MongoDB to interpret (run one first, or "
                "set AI_E2E_JOB_ID)")

        cls.server, baseUrl = _startGateway()
        _Handler.served = []
        _Handler.streamed = 0

        from src.conf import serverconf
        cls._saved = dict(serverconf.AI_PROVIDERS[serverconf.AI_LLM_PROVIDER])
        serverconf.AI_PROVIDERS[serverconf.AI_LLM_PROVIDER].update(
            {"api_base": baseUrl, "api_key": "stub", "model": "stub-model"})

        # The SDK client is built once and cached; force a rebuild so it picks
        # up the stand-in gateway, and switch pacing off -- the stub answers
        # instantly and a 55 rpm gait would only slow the suite.
        os.environ["AI_LLM_MAX_RPM"] = "0"
        import src.classes.AIInterpret.agent as agent
        agent._sdk_configured = False
        agent._MODEL_OBJ = None

        # Keep the run short: the point is the contract, not the breadth.
        cls._savedBudgets = (agent.AI_MAX_PATHWAYS,
                             agent.AI_MAX_SEARCH_TASKS,
                             agent.AI_PAPERS_PER_SEARCH_TASK)
        agent.AI_MAX_PATHWAYS = 2
        agent.AI_MAX_SEARCH_TASKS = 1
        agent.AI_PAPERS_PER_SEARCH_TASK = 2

        # Running the pipeline overwrites this job's stored interpretation.
        # That record may be a real report someone wants, so keep the previous
        # document and put it back in tearDownClass -- a test must not cost a
        # user their analysis just by being run.
        from src.common.DAO.AIInterpretDAO import AIInterpretDAO
        cls._priorRecord = AIInterpretDAO().find_by_job_id(cls.jobID)

        from src.classes.AIInterpret.agent import run_ai_agent
        from src.paintomicsserver import Response
        try:
            run_ai_agent(cls.jobID, "End-to-end stub run.", Response())
        except Exception as exc:                       # network, Mongo, R...
            cls.server.shutdown()
            cls._restorePriorRecord()
            raise unittest.SkipTest("the agent workflow could not run: %s" % exc)

        cls.stored = AIInterpretDAO().find_by_job_id(cls.jobID)

        # Could the bibliographic half of a reference be obtained at all?
        #
        # PubMed is deliberately not stubbed -- the module docstring above says
        # why: a PMID has to come from a real lookup or the reference tests mean
        # nothing. But the offline sweep runs with outbound network refused
        # (scripts/ci/no_network), so retrieval returns no papers,
        # render_references_section has nothing to resolve, and the four
        # reference tests below assert against a report that could not have
        # carried a reference in the first place.
        #
        # run_ai_agent is fail-soft about that: no papers is not an exception,
        # so the SkipTest above never fired and the suite failed instead -- which
        # is why it has been carried in run_all.BASELINE as "4 failures: the stub
        # gateway cannot satisfy the quote extractor" rather than fixed. A test
        # that cannot pass in the environment CI runs it in is not evidence of
        # anything. The four tests that do not depend on retrieval are untouched
        # and still assert on every run.
        # Read `stats.papers_retrieved`, which this run wrote, and fall back to
        # the `papers` list only when that key is absent altogether -- never as
        # an `or`. The document is updated field by field rather than replaced,
        # so a job that was interpreted for real once keeps that run's `papers`
        # array: measured here, a stub run recorded papers_retrieved = 0 beside
        # 27 leftover papers. Treating the longer list as this run's evidence is
        # how a "did anything happen" check ends up answering about a different
        # run entirely.
        stats = (cls.stored or {}).get("stats") or {}
        cls.papersRetrieved = (
            stats["papers_retrieved"] if "papers_retrieved" in stats
            else len((cls.stored or {}).get("papers") or []))

    @classmethod
    def _restorePriorRecord(cls):
        """Put back whatever interpretation the job had before this test."""
        prior = getattr(cls, "_priorRecord", None)
        jobID = getattr(cls, "jobID", None)
        if not jobID:
            return
        try:
            from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
            from pymongo import MongoClient
            collection = (MongoClient(MONGODB_HOST, int(MONGODB_PORT))
                          ["PaintomicsDB"]["aiInterpretationCollection"])
            if prior:
                document = dict(prior)
                document.pop("_id", None)
                collection.replace_one({"jobID": jobID}, document, upsert=True)
            else:
                # There was nothing here before; leave nothing behind.
                collection.delete_many({"jobID": jobID})
        except Exception as exc:
            raise AssertionError(
                "could not restore this job's stored interpretation: %s" % exc)

        # Verify rather than assume: a restore that silently did not happen is
        # how a cached report was lost elsewhere while a row count still
        # matched either side. Counting is not checking.
        from src.common.DAO.AIInterpretDAO import AIInterpretDAO
        current = AIInterpretDAO().find_by_job_id(jobID)
        if (current or {}).get("report") != (prior or {}).get("report"):
            raise AssertionError(
                "the job's stored interpretation was not restored; the "
                "database still holds what this test wrote")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "server", None):
            cls.server.shutdown()
        cls._restorePriorRecord()
        from src.conf import serverconf
        if hasattr(cls, "_saved"):
            serverconf.AI_PROVIDERS[serverconf.AI_LLM_PROVIDER].update(cls._saved)
        if hasattr(cls, "_savedBudgets"):
            import src.classes.AIInterpret.agent as agent
            (agent.AI_MAX_PATHWAYS,
             agent.AI_MAX_SEARCH_TASKS,
             agent.AI_PAPERS_PER_SEARCH_TASK) = cls._savedBudgets
            # Un-cache the stub client so a later run rebuilds from live conf.
            agent._sdk_configured = False
            agent._MODEL_OBJ = None

    # -- the run happened at all -------------------------------------------

    def test_the_pipeline_called_the_gateway(self):
        self.assertTrue(_Handler.served,
                        "the pipeline made no LLM request at all")

    def test_the_sdk_calls_were_streamed(self):
        # The transport issues every completion as a stream (see
        # test_ai_sdk_transport for why); a run that reached the gateway
        # without one streamed request would mean the shim is not on the path.
        self.assertGreater(_Handler.streamed, 0,
                           "no SDK call arrived at the gateway as a stream")

    def test_the_run_is_recorded_as_done(self):
        self.assertEqual((self.stored or {}).get("status"), "done")

    def test_a_report_was_produced(self):
        self.assertTrue((self.stored or {}).get("report"),
                        "the run finished with no report")

    # -- the references contract, which is what regressed before ------------

    def _requireRetrievedPapers(self):
        """Skip, rather than fail, when nothing could be retrieved.

        Every assertion below is about what the pipeline does *with* retrieved
        papers. With none -- PubMed unreachable, which is how the offline sweep
        runs -- there is no reference to render and the assertion has nothing to
        say. See the note in setUpClass.
        """
        if not self.papersRetrieved:
            self.skipTest(
                "no paper was retrieved, so no reference could be rendered "
                "from ground truth. PubMed is deliberately not stubbed and "
                "this run has no network; give the suite one to exercise the "
                "references contract.")

    def test_the_report_carries_a_references_section(self):
        """The defect: the section was absent in three runs of four."""
        self._requireRetrievedPapers()
        self.assertIn("### References", (self.stored or {}).get("report", ""),
                      "no References section was rendered")

    def test_the_reference_metadata_comes_from_the_paper_index(self):
        """A PMID cannot come from the stub -- only from a real lookup."""
        self._requireRetrievedPapers()
        report = (self.stored or {}).get("report", "")

        self.assertIn("PMID:", report,
                      "the reference carries no PMID, so it was not built from "
                      "retrieved papers")

    def test_verification_actually_ran(self):
        """`citations_checked: 0` while reporting done was the silent failure."""
        self._requireRetrievedPapers()
        verification = (self.stored or {}).get("verification") or {}

        self.assertTrue(verification.get("references_section_found"),
                        "verification found no section to check")
        self.assertGreater(verification.get("citations_checked", 0), 0,
                           "verification checked nothing and still finished")

    def test_no_citation_is_left_dangling(self):
        self._requireRetrievedPapers()
        verification = (self.stored or {}).get("verification") or {}

        self.assertEqual(verification.get("failed_citations") or [], [],
                         "a citation could not be resolved")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobID", default=None)
    args, remaining = parser.parse_known_args()
    if args.jobID:
        global JOB_ID
        JOB_ID = args.jobID

    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
