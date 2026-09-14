#!/usr/bin/env python3
"""Nothing leaves for the AI service unless the job's owner asked for it.

Why this exists
---------------
The upload page asks for consent in as many words -- "Enable AI pathway
interpretation (sends analysis summaries to external AI service)" -- and the
answer is stored on the job:

    jobInstance.setAIConsent(formFields.get("aiConsent", "false"))

It was then read in four places, all of them putting it into a response, and
checked in none. `aiInterpretInitiate` verified the session, the feature flag,
that a jobID was supplied and that the job existed, and started the pipeline.

Measured against a job whose stored record says aiConsent False -- a user who
had cleared the box -- posting its id to the endpoint returned success, and the
run went through triage, search planning, 8.1 seconds of literature retrieval,
interpretation and synthesis, reaching

    https://llm.iiia.es/v1/chat/completions

It stopped there only because this machine has no API key. A deployment with one
would have sent the analysis summary. The PubMed queries need no key and had
already gone out, so the 401 was not a saving grace.

Job ids are not secret -- the results page prints a shareable URL -- so the
decision has to be enforced on the server rather than by the client choosing not
to ask.

Checked statically. Standing the handler up needs a Flask request, a queue and
MongoDB; what matters is that the consent is consulted before the pipeline is
reached, and that reads off the source. Comments are stripped so a commented-out
check does not count. The behaviour itself was verified against the running
server: the declined job is now refused with a message naming the setting, while
a job that did consent still starts.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_ai_consent_enforced
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


def _stripComments(text):
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


class AIConsentEnforcedTest(unittest.TestCase):

    def setUp(self):
        self.handlers = _handlers()
        self.initiate = self.handlers.get("aiInterpretInitiate")
        if self.initiate is None:
            self.fail("aiInterpretInitiate not found; this test is looking in "
                      "the wrong place")

    def test_the_initiate_handler_consults_the_consent(self):
        self.assertIn("getAIConsent", _stripComments(self.initiate),
                      "the AI pipeline can be started for a job whose owner "
                      "declined, sending their analysis to an external service")

    def test_the_consent_is_not_only_a_comment(self):
        """A commented-out check would read as present without doing anything."""
        stripped = _stripComments(self.initiate)

        self.assertIn("getAIConsent", stripped)
        self.assertIn("raise", stripped,
                      "consulting the consent is not enough; declining has to "
                      "stop the run")

    def test_the_check_comes_before_the_work_is_queued(self):
        """Consent after enqueue would still send the data."""
        stripped = _stripComments(self.initiate)
        consentAt = stripped.find("getAIConsent")
        enqueueAt = stripped.find("enqueue")

        self.assertNotEqual(consentAt, -1, "no consent check at all")
        if enqueueAt != -1:
            self.assertLess(consentAt, enqueueAt,
                            "the consent is checked after the pipeline is "
                            "queued, which is too late to stop anything")

    def test_the_pre_job_handler_refuses_before_it_sends(self):
        """The request-borne consent has to stop the call, not just be read.

        test_every_outbound_handler_checks_consent accepts
        `formFields.get("aiConsent")` as a consent check, which on its own is
        satisfied by a handler that reads the field and ignores it. This is the
        half that makes accepting it safe: the check must raise, and it must do
        so before the LLMClient exists -- the same "before the work is queued"
        question the initiate handler is asked above, in the form this handler
        takes, where the outbound moment is the construction of the client
        rather than an enqueue.

        Read off the syntax tree rather than the text. The first version of
        this asked whether "raise" appeared between the consent read and the
        LLMClient, and a mutation that deleted the refusal outright still
        passed it -- the handler raises for malformed input and for an empty
        header set in between, and either one satisfied the substring. The
        raise has to belong to the branch that tests the consent.
        """
        source = self.handlers.get("aiGenerateExpDesign")
        if source is None:
            self.skipTest("aiGenerateExpDesign not found")

        tree = ast.parse(_stripComments(source))

        def mentionsConsent(node):
            return any(isinstance(sub, ast.Constant) and sub.value == "aiConsent"
                       for sub in ast.walk(node))

        refusals = [node for node in ast.walk(tree)
                    if isinstance(node, ast.If)
                    and mentionsConsent(node.test)
                    and any(isinstance(sub, ast.Raise) for sub in ast.walk(node))]

        self.assertTrue(refusals,
                        "no branch tests the consent the request carries and "
                        "raises, so a request without consent reaches the AI "
                        "service")

        clients = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name)
                   and node.func.id == "LLMClient"]
        if clients:
            self.assertLess(min(r.lineno for r in refusals),
                            min(c.lineno for c in clients),
                            "consent is checked after the LLM client is built, "
                            "which is too late to stop the send")

    def test_the_message_names_the_setting(self):
        """So the user knows which box to tick rather than just being refused."""
        stripped = _stripComments(self.initiate)

        self.assertIn("AI pathway interpretation", stripped,
                      "the refusal should name the setting to enable")

    def test_the_stored_default_is_no(self):
        """A job that never said anything must not count as consenting."""
        from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob

        job = PathwayAcquisitionJob("CONSENTTEST", None, "/tmp/")

        self.assertFalse(job.getAIConsent(),
                         "a fresh job defaults to consenting, so anything that "
                         "forgets to set it opts the user in")

    def test_only_an_explicit_yes_counts(self):
        from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob

        job = PathwayAcquisitionJob("CONSENTTEST", None, "/tmp/")
        for value in ("false", "False", "", None, 0, "no", "maybe"):
            with self.subTest(value=value):
                job.setAIConsent(value)
                self.assertFalse(job.getAIConsent(),
                                 "%r was treated as consent" % (value,))

        for value in ("true", "True", True):
            with self.subTest(value=value):
                job.setAIConsent(value)
                self.assertTrue(job.getAIConsent(),
                                "%r should be consent" % (value,))


    def test_every_outbound_handler_checks_consent(self):
        """The initiate gate alone left three other ways out.

        Chat, the per-pathway interpretation and the experiment-design helper
        each build an LLMClient and send job context to the same service, and
        none of them consulted the consent. Any handler that reaches outward
        has to ask.

        Consent has two homes, because one handler runs before a job exists.
        `aiGenerateExpDesign` drafts the experiment design from the column
        headers of files the user has only picked, on step 1 -- there is no
        jobInstance to call getAIConsent on, so the request carries the answer
        and the handler refuses without it. That is the same guarantee reached
        by the only means available at that point, not a weaker one, so this
        accepts it; but it accepts one exact expression rather than the word
        "aiConsent" appearing anywhere, because the failure being prevented is
        a handler that mentions consent and never tests it.
        """
        REQUEST_BORNE_CONSENT = 'formFields.get("aiConsent")'

        unguarded = []
        for name, source in self.handlers.items():
            if name.startswith("_"):
                continue
            body = _stripComments(source or "")
            sendsOutward = "LLMClient(" in body or "run_ai_pipeline" in body
            asks = ("getAIConsent" in body or "_consented(" in body
                    or REQUEST_BORNE_CONSENT in body)
            if sendsOutward and not asks:
                unguarded.append(name)

        self.assertEqual(sorted(unguarded), [],
                         "these send job data to the external service without "
                         "checking consent: %s" % sorted(unguarded))

    def test_the_walk_start_checks_consent_before_it_queues(self):
        """A walk sends the job's values to the model gateway from a queue
        worker, so the refusal has to come before the walk is filed."""
        stripped = _stripComments(self.handlers.get("aiWalkStart", ""))
        consentAt = stripped.find("getAIConsent")
        queueAt = stripped.find("_enqueueWalk(")
        self.assertNotEqual(consentAt, -1, "aiWalkStart files a walk without asking for consent")
        self.assertNotEqual(queueAt, -1, "aiWalkStart no longer files the walk here; update this test")
        self.assertLess(consentAt, queueAt, "the consent is checked after the walk is filed")
        self.assertIn("raise", stripped[consentAt:queueAt])

    def test_the_read_only_routes_are_not_gated(self):
        """Viewing a report already produced must keep working.

        Four jobs on this machine have a finished report but aiConsent False.
        Gating the read would hide results the user can already see, which
        protects nobody -- the sending already happened.
        """
        for name in ("aiInterpretStatus", "aiInterpretReport"):
            body = _stripComments(self.handlers.get(name, ""))
            with self.subTest(handler=name):
                self.assertNotIn("_consented(", body,
                                 "%s refuses to show a stored report, which "
                                 "hides results rather than preventing a send"
                                 % name)

    def test_an_unloadable_job_does_not_count_as_consent(self):
        """_consented must fail closed."""
        import src.servlets.AIInterpretServlet as servlet

        original = servlet.JobInformationManager

        class _NoJob:
            def loadJobInstance(self, jobID):
                return None

        servlet.JobInformationManager = lambda: _NoJob()
        try:
            self.assertFalse(servlet._consented("NOSUCHJOB"),
                             "a job that cannot be loaded was treated as "
                             "having consented")
        finally:
            servlet.JobInformationManager = original


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
