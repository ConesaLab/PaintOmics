#!/usr/bin/env python3
"""Ask the configured LLM gateway one trivial question; say whether it answered.

    python src/AdminTools/check_llm_gateway.py [--timeout 60] [--json]

Exit status:  0  the gateway answered
              1  it did not (refused, timed out, unreachable, or answered junk)
              2  nothing to ask: no API key, or AI interpretation is off

Why this exists
---------------
Every AI feature -- the pathway interpretation, the input converter, Step 2's
"Choose for me" -- ends at the same gateway, and until now nothing asked it a
question except a user. When it stopped answering (llm.iiia.es, 2026-08-21, an
hour of accepted connections and no bytes back) the first report was a user's,
and the symptom they described was "the feature is broken". The smoke test
passed throughout: it checked the containers, not the thing the containers
call.

One question, a handful of tokens, with a hard budget: cheap enough to run
after every deploy (deploy/smoke-test.sh), every night (the CI job that holds
the key as a secret), and by hand when someone says the AI is down. It uses
the same LLMClient the features use, against the same configuration, so it
answers for them and not for some simpler path.

Configuration comes from src/conf/serverconf.py when that exists (inside the
container it always does) and from the environment otherwise -- the same
AI_LLM_PROVIDER / AI_<PROVIDER>_API_BASE / _API_KEY / _MODEL names the
template reads, so a CI runner with the key in its environment needs nothing
else. The key is never printed.
"""
import argparse
import json
import os
import sys
import time

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if SERVER_ROOT not in sys.path:
    sys.path.insert(0, SERVER_ROOT)

# The same defaults src/resources/example_serverconf.py carries, for a run
# with no serverconf.py at all. Kept minimal on purpose: a provider that is not
# here is configured through serverconf, where it belongs.
_ENV_DEFAULTS = {
    "csic": ("https://llm.iiia.es/v1", "deepseek-ai/DeepSeek-V4-Flash-0731"),
    "dashscope": ("https://coding-intl.dashscope.aliyuncs.com/v1", "qwen3.5-plus"),
    "openrouter": ("https://openrouter.ai/api/v1", "anthropic/claude-3.5-sonnet"),
}

QUESTION = "Reply with the single word OK and nothing else."


def resolve_provider():
    """(provider name, {api_base, api_key, model}, interpretation enabled)."""
    try:
        from src.conf.serverconf import (AI_PROVIDERS, AI_LLM_PROVIDER,
                                         AI_INTERPRETATION_ENABLED)
        name = AI_LLM_PROVIDER
        config = dict(AI_PROVIDERS.get(name) or {})
        enabled = bool(AI_INTERPRETATION_ENABLED)
    except ImportError:
        name = os.getenv("AI_LLM_PROVIDER", "csic")
        prefix = "AI_%s_" % name.replace("-", "_").upper()
        base, model = _ENV_DEFAULTS.get(name, ("", ""))
        config = {
            "api_base": os.getenv(prefix + "API_BASE", base),
            "api_key": os.getenv(prefix + "API_KEY", ""),
            "model": os.getenv(prefix + "MODEL", model),
        }
        enabled = os.getenv("AI_INTERPRETATION_ENABLED", "true").lower() == "true"
    return name, config, enabled


def probe(config, provider_name="csic", timeout=60):
    """Send QUESTION; return a dict that says what happened. Never raises.

    `timeout` bounds the whole call in seconds, connection included: one
    attempt, no retry. A check that retries hides the intermittent failures
    it exists to catch.
    """
    from src.classes.AIInterpret.llm_client import LLMClient, MissingAPIKeyError

    result = {
        "ok": False, "provider": provider_name,
        "host": _host(config.get("api_base", "")),
        "model": config.get("model", ""), "seconds": None, "reply": None,
        "error": None,
    }
    try:
        client = LLMClient(config, provider_name)
    except MissingAPIKeyError as ex:
        result["error"] = str(ex)
        result["configured"] = False
        return result
    result["configured"] = True

    started = time.monotonic()
    try:
        reply = client.complete(
            [{"role": "user", "content": QUESTION}],
            max_tokens=8, temperature=0,
            timeout=(min(10, timeout), timeout),
            max_attempts=1, budget_seconds=timeout)
    except Exception as ex:  # noqa: BLE001 -- every failure is one verdict here
        result["seconds"] = round(time.monotonic() - started, 2)
        result["error"] = "%s: %s" % (type(ex).__name__, str(ex).strip() or "no detail")
        return result
    result["seconds"] = round(time.monotonic() - started, 2)
    result["reply"] = (reply or "").strip()
    # Any non-empty completion counts. Pinning the exact word would fail a
    # healthy gateway whose model adds a full stop, and the question is
    # "does it answer", not "does it obey".
    result["ok"] = bool(result["reply"])
    if not result["ok"]:
        result["error"] = "the gateway returned an empty completion"
    return result


def _host(api_base):
    try:
        from urllib.parse import urlparse
        return urlparse(api_base).hostname or api_base
    except Exception:
        return api_base


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--timeout", type=int, default=60,
                        help="seconds for the whole call (default 60)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    name, config, enabled = resolve_provider()
    if not enabled:
        verdict = {"ok": False, "configured": False, "provider": name,
                   "error": "AI_INTERPRETATION_ENABLED is false on this server"}
        _emit(verdict, args.json)
        return 2

    verdict = probe(config, name, timeout=args.timeout)
    _emit(verdict, args.json)
    if verdict["ok"]:
        return 0
    return 2 if not verdict.get("configured", True) else 1


def _emit(verdict, as_json):
    if as_json:
        print(json.dumps(verdict, sort_keys=True))
        return
    where = "%s (%s, %s)" % (verdict.get("provider"), verdict.get("model") or "?",
                             verdict.get("host") or "?")
    if verdict["ok"]:
        print("OK   %s answered in %s s: %r" % (where, verdict["seconds"], verdict["reply"]))
    elif not verdict.get("configured", True):
        print("SKIP %s: %s" % (where, verdict["error"]))
    else:
        after = "" if verdict.get("seconds") is None else " after %s s" % verdict["seconds"]
        print("FAIL %s did not answer%s: %s" % (where, after, verdict["error"]))


if __name__ == "__main__":
    sys.exit(main())
