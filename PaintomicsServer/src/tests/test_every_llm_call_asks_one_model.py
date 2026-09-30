#!/usr/bin/env python3
"""Every LLM call asks the gateway's `default/llm` alias, wherever it is configured.

Why this exists
---------------
On 2026-09-30 every LLM call was switched from the pinned
`deepseek-ai/DeepSeek-V4-Flash-0731` snapshot to the CSIC gateway's own
`default/llm` alias. The model name is not written in one place: the config
template, the container's compose file, the operator's env.example, the
developer's .env.example, the gateway check's no-serverconf defaults and the
CI review's gateway job (.github/workflows/code-review.yml) each carry it. A switch that misses one of them is a partial switch that nothing
reports -- paintomics.org's compose default would keep asking DeepSeek while
the template, and every test, says otherwise.

Also pinned: with the alias as the model and the alias as the default
fallback, the ladder has ONE rung. `model_fallback.fallback_models` drops a
fallback equal to the model, so a failing alias is not asked twice.

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

MODEL = "default/llm"

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


class EveryPlaceNamesTheAlias(unittest.TestCase):

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


class TheAliasIsAskedOnce(unittest.TestCase):

    def test_the_shipped_ladder_has_one_rung(self):
        from src.classes.AIInterpret import model_fallback
        self.assertEqual(model_fallback.candidates(_template_provider(), "csic"), [MODEL])

    def test_a_pinned_model_still_falls_back_to_the_alias(self):
        """The ladder is still there for a deployment that pins a concrete id."""
        from src.classes.AIInterpret import model_fallback
        provider = dict(_template_provider(), model="deepseek-ai/DeepSeek-V4-Flash-0731")
        self.assertEqual(model_fallback.candidates(provider, "csic"),
                         ["deepseek-ai/DeepSeek-V4-Flash-0731", MODEL])


if __name__ == "__main__":
    unittest.main()
