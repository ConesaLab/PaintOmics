#!/usr/bin/env python3
"""Every LLM call asks the same model, wherever it is configured.

Why this exists
---------------
The model name is not written in one place: the config template, the
container's compose file, the operator's env.example, the developer's
.env.example, the gateway check's no-serverconf defaults and the CI review's
gateway job (.github/workflows/code-review.yml) each carry it. On 2026-09-30
every LLM call was switched to the CSIC gateway's `default/llm` alias and,
the same day, back to the pinned `deepseek-ai/DeepSeek-V4-Flash-0731`
snapshot: on the STATegra example the alias (Qwen3.8-Flash-Next) kept 1
statement and cited 2 papers where the snapshot kept 8 and cited 11, with or
without its thinking mode. A switch that misses one of the places is a partial
switch that nothing reports -- paintomics.org's compose default would keep
asking one model while the template, and every test, says another.

Also pinned: the ladder the template ships -- the snapshot first, then the
alias when the snapshot is not being served -- and that a fallback equal to
the model is dropped, so a deployment that sets the alias as its model does
not ask it twice.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_every_llm_call_asks_one_model
"""
import os
import re
import sys
import unittest

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(SERVER_ROOT)
sys.path.insert(0, SERVER_ROOT)

from src.tests.test_release_hygiene import _execTemplate  # noqa: E402

MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
ALIAS = "default/llm"

TEMPLATE = os.path.join(SERVER_ROOT, "src", "resources", "example_serverconf.py")
DOTENV_EXAMPLE = os.path.join(SERVER_ROOT, ".env.example")
COMPOSE = os.path.join(REPO_ROOT, "deploy", "compose.yaml")
ENV_EXAMPLE = os.path.join(REPO_ROOT, "deploy", "env.example")
REVIEW_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "code-review.yml")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _one(pattern, text, where):
    found = re.findall(pattern, text, re.MULTILINE)
    if len(found) != 1:
        raise AssertionError("%s: expected one AI_CSIC_MODEL setting, found %r" % (where, found))
    return found[0]


def _template_provider():
    """The csic provider as a deployment with no AI_CSIC_* variables gets it."""
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("AI_CSIC_")}
    try:
        return _execTemplate(TEMPLATE)["AI_PROVIDERS"]["csic"]
    finally:
        os.environ.update(saved)


class EveryPlaceNamesTheSameModel(unittest.TestCase):

    def test_the_config_template(self):
        self.assertEqual(_template_provider()["model"], MODEL)

    def test_the_container_default(self):
        self.assertEqual(_one(r"^\s+AI_CSIC_MODEL: \$\{AI_CSIC_MODEL:-([^}]*)\}",
                              _read(COMPOSE), "deploy/compose.yaml"), MODEL)

    def test_the_operator_env_example(self):
        self.assertEqual(_one(r"^AI_CSIC_MODEL=(\S*)", _read(ENV_EXAMPLE),
                              "deploy/env.example"), MODEL)

    def test_the_developer_dotenv_example(self):
        self.assertEqual(_one(r"^#?\s*AI_CSIC_MODEL=(\S*)", _read(DOTENV_EXAMPLE),
                              "PaintomicsServer/.env.example"), MODEL)

    def test_the_gateway_check_without_a_serverconf(self):
        from src.AdminTools import check_llm_gateway
        self.assertEqual(check_llm_gateway._ENV_DEFAULTS["csic"][1], MODEL)

    def test_the_ci_review_on_the_gateway(self):
        """Its three Claude model aliases and its --model all name the gateway's model."""
        text = _read(REVIEW_WORKFLOW)
        aliases = re.findall(r"^\s+ANTHROPIC_DEFAULT_(?:HAIKU|SONNET|OPUS)_MODEL: (\S+)", text, re.MULTILINE)
        self.assertEqual(aliases, [MODEL] * 3)
        self.assertEqual(re.findall(r"^\s+--model (\S+)", text, re.MULTILINE), [MODEL])


class TheShippedLadder(unittest.TestCase):

    def test_the_snapshot_then_the_alias(self):
        from src.classes.AIInterpret import model_fallback
        self.assertEqual(model_fallback.candidates(_template_provider(), "csic"), [MODEL, ALIAS])

    def test_the_alias_as_the_model_is_asked_once(self):
        """AI_CSIC_MODEL=default/llm: its own fallback is dropped, not asked twice."""
        from src.classes.AIInterpret import model_fallback
        provider = dict(_template_provider(), model=ALIAS)
        self.assertEqual(model_fallback.candidates(provider, "csic"), [ALIAS])


if __name__ == "__main__":
    unittest.main()
