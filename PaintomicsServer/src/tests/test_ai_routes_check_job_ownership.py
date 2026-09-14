#!/usr/bin/env python3
"""A job id is not authorisation to read the AI interpretation of that job.

Why this exists
---------------
`test_ai_consent_enforced` covers a different question: whether the job's
owner agreed to anything being sent OUT to the external service. It says
nothing about who may ask. Both were needed, and only the first was there.

Every `/ai_interpret_*` route verified the session with

    UserSessionManager().isValidUser(userID, sessionToken)

which deliberately admits the anonymous "nologin" caller -- `user_id == 'None'`
with no token returns True, because the application supports jobs that belong
to nobody. So the session check passed for a caller with no cookies at all, and
nothing after it consulted the job's owner. Meanwhile the results page prints a
shareable `?jobID=...` URL, so ids travel by design.

Measured against a running server, on a job whose stored record said
userID '99999' and allowSharing False, from a caller sending no cookies:

    /pa_recover_job       -> refused, "Invalid Job ID (...) for current user."
    /ai_interpret_report  -> 10,345 characters of report and its 28 papers
    /ai_interpret_status  -> the job's progress
    /ai_interpret_chat    -> an answer about the job's genes, which is an LLM
                             call billed to this deployment, made against
                             someone else's expression values

`pathwayAcquisitionRecoverJob` already had the rule. The AI routes just never
asked it, so the same job was public through one door and private through
another.

Two halves, because either alone passes for the wrong reason:

  * the decision table, exercised against `_requireJobAccess` with a stubbed
    loader. This is the half that would catch a gate that refuses everything,
    which "the call is present" cannot;
  * the presence of the call in each job-scoped handler, read off
    the syntax tree with comments stripped, so a handler added later without a
    gate fails here rather than shipping open.

`ai_provider` and `ai_generate_exp_design` are deliberately not in the list:
neither takes a jobID -- the first reports which gateway this server uses, the
second reads column headers for a job that does not exist yet and carries its
own consent check.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_ai_routes_check_job_ownership
"""
import ast
import io
import os
import sys
import tokenize
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

SERVLET = os.path.join(os.path.dirname(__file__),
                       "../servlets/AIInterpretServlet.py")

# Every route that takes a jobID and answers with, or acts on, that job.
JOB_SCOPED_HANDLERS = [
    "aiInterpretInitiate",
    "aiInterpretStatus",
    "aiInterpretReport",
    "aiInterpretChat",
    "aiWalkStart",
    "aiWalkStatus",
]

GATE = "_requireJobAccess"


def _stripComments(text):
    """Comments are prose, not code; a gate described in one is not a gate."""
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(text).readline)
                  if t.type != tokenize.COMMENT]
        return tokenize.untokenize(tokens)
    except Exception:
        return text


def _handlers():
    with open(SERVLET, "r", encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    tree = ast.parse(source)
    return {node.name: ast.get_source_segment(source, node)
            for node in tree.body if isinstance(node, ast.FunctionDef)}


class _FakeJob(object):
    """The three fields the rule reads, and nothing else."""

    def __init__(self, userID, allowSharing):
        self._userID = userID
        self._allowSharing = allowSharing

    def getUserID(self):
        return self._userID

    def getAllowSharing(self):
        return self._allowSharing


class AIRouteOwnershipDecisionTest(unittest.TestCase):
    """What the gate admits and what it refuses.

    The import is inside the test rather than at module scope: importing the
    servlet pulls in the LLM client and the server configuration, and this file
    should fail on a missing gate, not on a missing API key.
    """

    def _gate(self):
        try:
            from src.servlets import AIInterpretServlet
        except Exception as ex:  # pragma: no cover - environment, not behaviour
            self.skipTest("AIInterpretServlet is not importable here: %s" % ex)

        gate = getattr(AIInterpretServlet, GATE, None)
        if gate is None:
            self.fail("%s does not exist. The AI routes have no ownership "
                      "gate, so any caller holding a job id can read that "
                      "job's report and chat against its data." % GATE)
        return AIInterpretServlet, gate

    def _run(self, job, callerID):
        module, gate = self._gate()
        original = module.JobInformationManager

        class _Loader(object):
            def loadJobInstance(self, jobID):
                return job

        class _Manager(object):
            def __call__(self):
                return _Loader()

        module.JobInformationManager = _Manager()
        try:
            return gate("JOB123", callerID)
        finally:
            module.JobInformationManager = original

    def test_a_stranger_is_refused_someone_elses_private_job(self):
        """The measured leak, as a test."""
        with self.assertRaises(Exception) as caught:
            self._run(_FakeJob("99999", False), None)

        self.assertIn("Invalid Job ID", str(caught.exception),
                      "the refusal should read the same as pa_recover_job's, "
                      "which says the id is not valid for this caller rather "
                      "than confirming it exists and belongs to someone else")

    def test_a_logged_in_stranger_is_refused_too(self):
        """Holding *an* account is not holding *the* account."""
        with self.assertRaises(Exception):
            self._run(_FakeJob("99999", False), "12345")

    def test_a_guest_job_stays_readable_by_anyone(self):
        """Anonymous jobs belong to nobody, and that is deliberate.

        `isValidUser` admits the nologin caller on purpose and those jobs live
        under CLIENT_TMP/nologin. Narrowing this would break guest use of the
        whole application while protecting nothing, so the gate has to let it
        through -- exactly as pa_recover_job does.
        """
        job = _FakeJob(None, False)
        self.assertIs(self._run(job, None), job)

    def test_a_shared_job_stays_readable_by_anyone(self):
        """"Allow sharing" is the owner saying yes; the gate must honour it."""
        job = _FakeJob("99999", True)
        self.assertIs(self._run(job, "12345"), job)

    def test_the_owner_is_admitted(self):
        job = _FakeJob("99999", False)
        self.assertIs(self._run(job, "99999"), job)

    def test_the_owner_is_admitted_when_the_id_types_differ(self):
        """The cookie is a string; the stored id need not be.

        pa_recover_job compares `jobInstance.getUserID() != userID` with no
        coercion on the right, so an int 99999 against the cookie "99999" is
        unequal and the owner is refused their own job. Whatever that route
        does, this gate compares as strings.
        """
        job = _FakeJob(99999, False)
        self.assertIs(self._run(job, "99999"), job)

    def test_an_unknown_job_is_refused_rather_than_admitted(self):
        """A job that will not load must not read as "no owner, so public"."""
        with self.assertRaises(Exception):
            self._run(None, None)


class AIRouteOwnershipWiringTest(unittest.TestCase):
    """The gate exists and every job-scoped handler actually calls it."""

    def setUp(self):
        self.handlers = _handlers()
        missing = [name for name in JOB_SCOPED_HANDLERS
                   if name not in self.handlers]
        if missing:
            self.fail("handler(s) not found, so this test is looking in the "
                      "wrong place: " + ", ".join(missing))

    def test_every_job_scoped_handler_calls_the_gate(self):
        unguarded = []
        for name in JOB_SCOPED_HANDLERS:
            stripped = _stripComments(self.handlers[name])
            tree = ast.parse(stripped)
            calls = [node for node in ast.walk(tree)
                     if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name)
                     and node.func.id == GATE]
            if not calls:
                unguarded.append(name)

        self.assertEqual([], unguarded,
                         "these AI routes accept a bare job id as "
                         "authorisation: " + ", ".join(unguarded))

    def test_the_initiate_gate_precedes_the_enqueue(self):
        """Refusing after the pipeline is queued refuses nothing."""
        stripped = _stripComments(self.handlers["aiInterpretInitiate"])
        gateAt = stripped.find(GATE)
        enqueueAt = stripped.find("enqueue")

        self.assertNotEqual(gateAt, -1, "aiInterpretInitiate has no gate")
        if enqueueAt != -1:
            self.assertLess(gateAt, enqueueAt,
                            "the ownership check runs after the AI pipeline "
                            "is queued, which is too late to stop it")

    def test_the_chat_gate_precedes_the_llm_client(self):
        """The chat turn is the outbound moment, and it is billed."""
        stripped = _stripComments(self.handlers["aiInterpretChat"])
        gateAt = stripped.find(GATE)
        llmAt = stripped.find("LLMClient(")

        self.assertNotEqual(gateAt, -1, "aiInterpretChat has no gate")
        if llmAt != -1:
            self.assertLess(gateAt, llmAt,
                            "the ownership check runs after the LLM client is "
                            "built, so the call goes out regardless")


if __name__ == "__main__":
    unittest.main(verbosity=2)
