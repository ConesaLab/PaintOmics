#!/usr/bin/env python3
"""The CSIC LLM gateway answers. Live: runs only where a key is present.

Why this exists
---------------
Every AI feature ends at one gateway (llm.iiia.es by default), and the only
monitor it had was a user noticing that the AI "does not work". This asks it
one eight-token question through the same LLMClient the features use and
fails if it does not answer inside the budget.

It is skipped, not failed, when no key is in the environment: the pull-request
gate runs offline and holds no secrets, and every developer checkout is in the
same position. The nightly workflow carries the key as the repository secret
AI_CSIC_API_KEY (the "put the key in a secret" decision of 2026-09-08) and is
where this actually runs; deploy/smoke-test.sh runs the same probe inside the
container after every deploy, with the container's own configuration.

Usage:
    cd PaintomicsServer
    AI_CSIC_API_KEY=... python -m src.tests.test_llm_gateway_live
"""
import os
import sys
import unittest

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SERVER_ROOT)

from src.AdminTools import check_llm_gateway as probe  # noqa: E402
from src.classes.AIInterpret.llm_client import env_var_for_provider  # noqa: E402

BUDGET_SECONDS = int(os.getenv("AI_GATEWAY_PROBE_SECONDS", "90"))


class TheGatewayAnswers(unittest.TestCase):

    def setUp(self):
        self.name, self.config, _ = probe.resolve_provider()
        key = (self.config.get("api_key") or "").strip()
        if not key:
            self.skipTest("no %s in the environment; the live gateway check runs "
                          "in the nightly workflow, which holds it as a secret"
                          % env_var_for_provider(self.name))

    def test_one_question_gets_an_answer_within_the_budget(self):
        verdict = probe.probe(self.config, self.name, timeout=BUDGET_SECONDS)
        self.assertTrue(
            verdict["ok"],
            "%s (%s at %s) did not answer within %d s: %s" % (
                self.name, verdict.get("model"), verdict.get("host"),
                BUDGET_SECONDS, verdict.get("error")))
        # Slow is a warning worth seeing in the log, not a failure: the
        # conversion turn budgets 150 s and the interpreter far more.
        print("gateway answered in %s s: %r" % (verdict["seconds"], verdict["reply"]))


if __name__ == "__main__":
    unittest.main()
