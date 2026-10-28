#!/usr/bin/env python3
"""A model's leaked reasoning is never returned as its answer.

DeepSeek-V4-Flash behind the CSIC gateway writes its reasoning into the
answer's content and closes it with a bare "</think>", usually with no opening
tag. /ai_interpret_chat returned and stored that content verbatim: 39 of the 50
chat answers on paintomics.org (2026-10-28) began like the first shape below,
and the page rendered the reasoning as part of the answer.

The shapes are the stored ones, shortened. The gateway is a stand-in for
requests.post, as in test_llm_schema_json: nothing here reaches a network.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_llm_reasoning_is_not_the_answer
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret import llm_client as lc   # noqa: E402
from src.classes.AIInterpret.llm_client import strip_reasoning  # noqa: E402

ANSWER = "## FGFR3 in this dataset\n\nFGFR3 rises 2.1-fold in the tumour samples."
# A one-line preamble, the commonest shape.
SHORT_LEAK = "Now I have the data. Let me compose the answer.</think>" + ANSWER
# Several paragraphs of planning before the tag.
LONG_LEAK = ("The user asks about FGFR3 in the bladder cancer leg. Let me explain:\n\n"
             "1. FGFR3 is measured in two omics.\n2. The walk steps from FGFR3 to HRAS.\n\n"
             "I should cite the walk, not invent papers.</think>\n\n" + ANSWER)
PROVIDER = {"api_base": "https://reasoning.test/v1", "api_key": "k", "model": "m",
            "fallback_models": ""}


class StripReasoningTest(unittest.TestCase):

    def test_a_bare_closing_tag_ends_the_reasoning(self):
        self.assertEqual(strip_reasoning(SHORT_LEAK), ANSWER)

    def test_several_paragraphs_of_reasoning_go(self):
        self.assertEqual(strip_reasoning(LONG_LEAK), ANSWER)

    def test_a_complete_think_block_goes(self):
        self.assertEqual(strip_reasoning("<think>\nThe user wants FGFR3.\n</think>\n\n" + ANSWER), ANSWER)

    def test_a_block_inside_the_answer_goes_and_the_answer_stays(self):
        text = "Plan first.</think>FGFR3 rises. <think>check the fold</think>HRAS follows."
        self.assertEqual(strip_reasoning(text), "FGFR3 rises. HRAS follows.")

    def test_text_without_a_closing_tag_is_untouched(self):
        for text in (ANSWER, "  padded answer \n", "<think> never closed, cut at max_tokens"):
            self.assertIs(strip_reasoning(text), text)

    def test_reasoning_with_no_answer_after_it_is_kept(self):
        """Nothing at all would be stored and shown as a failed question."""
        for text in ("Let me think about FGFR3.</think>", "<think>only this</think>  \n"):
            self.assertEqual(strip_reasoning(text), text)

    def test_what_is_not_text_passes_through(self):
        self.assertIsNone(strip_reasoning(None))


class _Resp(object):
    """A requests.Response for one chat completion, plain or streamed."""
    status_code = 200
    headers = {}

    def __init__(self, message):
        self.message = message

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"index": 0, "finish_reason": "stop", "message": self.message}]}

    def iter_lines(self):
        content = self.message.get("content") or ""
        for start in range(0, len(content), 7):          # split mid-tag on purpose
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": content[start:start + 7]}}]})).encode()
        yield b"data: [DONE]"

    def close(self):
        pass


class ClientAnswersTest(unittest.TestCase):

    def setUp(self):
        self.real_post = lc.requests.post
        self.content = SHORT_LEAK

        def fake_post(url, headers=None, json=None, timeout=None, stream=False):
            return _Resp({"role": "assistant", "content": self.content})
        lc.requests.post = fake_post
        self.client = lc.LLMClient(PROVIDER, "csic")

    def tearDown(self):
        lc.requests.post = self.real_post

    def test_complete_returns_the_answer(self):
        self.assertEqual(self.client.complete([{"role": "user", "content": "FGFR3?"}]), ANSWER)

    def test_a_streamed_answer_is_cleaned_after_it_is_folded(self):
        self.content = LONG_LEAK
        self.assertEqual(self.client.complete([{"role": "user", "content": "FGFR3?"}], stream=True), ANSWER)

    def test_json_after_leaked_reasoning_still_parses(self):
        self.content = 'I will answer with the schema.</think>\n{"statements": [{"n": 1}]}'
        out = self.client.complete_json([{"role": "user", "content": "revise"}], "s", {"type": "object"},
                                        fallback_parser=lambda text: {"fallback": text})
        self.assertEqual(out, {"statements": [{"n": 1}]})

    def test_the_tool_loops_final_answer_is_cleaned(self):
        reply = self.client.complete_with_tools([{"role": "user", "content": "FGFR3?"}], [], lambda *a: "")
        self.assertEqual(reply, ANSWER)

    def test_a_null_final_answer_is_empty_text(self):
        self.content = None
        self.assertEqual(self.client.complete_with_tools([{"role": "user", "content": "?"}], [], lambda *a: ""), "")


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
