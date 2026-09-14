#!/usr/bin/env python3
"""A server with no LLM token says so, instead of failing at the gateway.

Why this exists
---------------
`AI_CSIC_API_KEY` is a secret, so it is empty on any checkout that has not been
handed one -- every developer machine, and any deployment whose operator has not
visited https://console.llm.iiia.es. `LLMClient` sent the empty value anyway:

    headers={"Authorization": f"Bearer {self.api_key}"}

which is the literal string "Bearer ", and the gateway answered

    401 {"error": {"message": "Authentication Error, Malformed API Key passed
    in. Ensure Key has `Bearer ` prefix.", ...}}

Measured on a local run (job 35qGA4v0e6, 2026-08-10): the request was accepted,
the pipeline was queued, and 13.2 seconds went into literature retrieval --
real PubMed and Europe PMC traffic against shared rate limits -- before the
first LLM call could fail. The run then died with a bare `HTTPError` naming a
URL, and the "Malformed API Key" text points the reader at a `Bearer ` prefix
that the code already sends. Nothing in that chain says "this server has no
token", which is the only true and actionable statement about it.

An unset key cannot start working later in the run, so it is worth one cheap
check up front: refuse at the point of request, name the setting, and send no
outward traffic at all.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_ai_missing_key_fails_fast
"""
import ast
import io
import os
import sys
import tokenize
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.llm_client import (LLMClient, MissingAPIKeyError,
                                                env_var_for_provider)

SERVLET = os.path.join(os.path.dirname(__file__),
                       "../servlets/AIInterpretServlet.py")


def _stripComments(text):
    """Comments must not satisfy a source assertion."""
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(text).readline)
                  if t.type != tokenize.COMMENT]
        return tokenize.untokenize(tokens)
    except Exception:
        return text


def _config(apiKey):
    return {"api_base": "https://llm.iiia.es/v1", "api_key": apiKey,
            "model": "stub-model"}


class TestClientRefusesABlankKey(unittest.TestCase):
    """The client is the one place every AI route passes through."""

    def testEmptyKeyRaises(self):
        with self.assertRaises(MissingAPIKeyError):
            LLMClient(_config(""))

    def testMissingKeyRaises(self):
        with self.assertRaises(MissingAPIKeyError):
            LLMClient({"api_base": "https://llm.iiia.es/v1", "model": "m"})

    def testNoneKeyRaises(self):
        with self.assertRaises(MissingAPIKeyError):
            LLMClient(_config(None))

    def testWhitespaceKeyRaises(self):
        """"Bearer    " is as unauthenticated as "Bearer "."""
        with self.assertRaises(MissingAPIKeyError):
            LLMClient(_config("   "))

    def testMessageNamesTheSettingNotTheBearerPrefix(self):
        """The gateway's own wording sends the reader after the wrong thing."""
        with self.assertRaises(MissingAPIKeyError) as caught:
            LLMClient(_config(""))
        message = str(caught.exception)
        self.assertIn("AI_CSIC_API_KEY", message)
        self.assertNotIn("Bearer", message)

    def testKeyIsStrippedOfStrayWhitespace(self):
        """A token pasted out of a console often carries a trailing newline."""
        self.assertEqual(LLMClient(_config(" sk-abc123\n")).api_key, "sk-abc123")

    def testARealKeyStillConstructs(self):
        """Regression: the existing end-to-end test builds a client on "stub"."""
        self.assertEqual(LLMClient(_config("stub")).api_key, "stub")


class TestEnvVarNaming(unittest.TestCase):
    """The message has to name the variable the operator actually sets."""

    def testKnownProviders(self):
        self.assertEqual(env_var_for_provider("csic"), "AI_CSIC_API_KEY")
        self.assertEqual(env_var_for_provider("openrouter"), "AI_OPENROUTER_API_KEY")
        self.assertEqual(env_var_for_provider("dashscope"), "AI_DASHSCOPE_API_KEY")

    def testUnknownProviderStillProducesAName(self):
        self.assertEqual(env_var_for_provider("my-gateway"), "AI_MY_GATEWAY_API_KEY")


class TestServletChecksBeforeSpendingAnything(unittest.TestCase):
    """Checked statically: the handler needs Flask, a queue and MongoDB.

    What matters is the order -- credentials consulted before the pipeline is
    enqueued -- and that reads off the source.
    """

    @classmethod
    def setUpClass(cls):
        with open(SERVLET) as handle:
            cls.source = _stripComments(handle.read())
        cls.tree = ast.parse(cls.source)

    def _initiate(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "aiInterpretInitiate":
                return node
        self.fail("aiInterpretInitiate not found")

    def testCredentialCheckPrecedesEnqueue(self):
        initiate = self._initiate()
        checkLine = enqueueLine = None
        for node in ast.walk(initiate):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "_requireLLMCredentials" and checkLine is None:
                checkLine = node.lineno
            # The walk is filed through _enqueueWalk, which calls the queue's
            # enqueue; either spelling is the moment work is committed.
            files = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "enqueue") or \
                    (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id == "_enqueueWalk")
            if files and enqueueLine is None:
                enqueueLine = node.lineno

        self.assertIsNotNone(checkLine, "aiInterpretInitiate never checks credentials")
        self.assertIsNotNone(enqueueLine, "aiInterpretInitiate no longer enqueues")
        self.assertLess(checkLine, enqueueLine,
                        "credentials are checked after the pipeline is queued, so "
                        "the PubMed traffic goes out before the refusal")

    def testRefusalIsAUserWarning(self):
        """UserWarning is what this servlet renders as a readable message."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_requireLLMCredentials":
                raises = [n for n in ast.walk(node) if isinstance(n, ast.Raise)]
                self.assertTrue(raises, "_requireLLMCredentials never refuses")
                for raise_ in raises:
                    exc = raise_.exc
                    name = exc.func.id if isinstance(exc, ast.Call) else getattr(exc, "id", None)
                    self.assertEqual(name, "UserWarning")
                return
        self.fail("_requireLLMCredentials not found")

    def testEveryLLMClientRouteIsCovered(self):
        """Chat and per-pathway interpretation build clients of their own."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name == "_requireLLMCredentials":
                continue  # the checker itself; constructing the client IS the check
            buildsClient = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "LLMClient" for n in ast.walk(node))
            if not buildsClient:
                continue
            checks = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_requireLLMCredentials" for n in ast.walk(node))
            self.assertTrue(
                checks,
                f"{node.name} builds an LLMClient without checking credentials, so "
                f"it fails at the gateway instead of naming the missing setting")


if __name__ == "__main__":
    unittest.main(verbosity=2)
