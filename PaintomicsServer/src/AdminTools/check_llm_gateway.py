#!/usr/bin/env python3
"""Ask the configured LLM gateway one trivial question; say whether it answered.

    python src/AdminTools/check_llm_gateway.py [--timeout 60] [--json]

Exit status:  0  the pinned model answered
              3  only a fallback model answered (features work, on another model)
              1  nothing answered (refused, timed out, unreachable, or junk)
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
answers for them and not for some simpler path. Every model on the ladder is
asked -- the pinned one and each fallback -- because "the site works" and
"the site works on the model the paper names" are different facts, and the
verdict line says which one is true.

Streamed, like the features: on 2026-09-08 the gateway answered a plain
request with a 500 and the same request streamed with the answer.

Configuration comes from src/conf/serverconf.py when that exists (inside the
container it always does) and from the environment otherwise -- the same
AI_LLM_PROVIDER / AI_<PROVIDER>_API_BASE / _API_KEY / _MODEL /
_FALLBACK_MODELS names the template reads, so a CI runner with the key in its
environment needs nothing else. The key is never printed.
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

EXIT_OK, EXIT_FAIL, EXIT_SKIP, EXIT_DEGRADED = 0, 1, 2, 3


def resolve_provider():
    """(provider name, {api_base, api_key, model[, fallback_models]}, enabled)."""
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
    """Send QUESTION to config["model"]; return a dict of what happened.

    Never raises. `timeout` bounds the whole call in seconds, connection
    included: one attempt, no retry, no fallback -- this measures ONE model,
    and a check that retries hides the intermittent failures it exists to
    catch.
    """
    from src.classes.AIInterpret.llm_client import LLMClient, MissingAPIKeyError

    result = {
        "ok": False, "provider": provider_name,
        "host": _host(config.get("api_base", "")),
        "model": config.get("model", ""), "seconds": None, "reply": None,
        "error": None,
    }
    try:
        client = LLMClient(dict(config, fallback_models=""), provider_name)
    except MissingAPIKeyError as ex:
        result["error"] = str(ex)
        result["configured"] = False
        return result
    result["configured"] = True

    started = time.monotonic()
    try:
        reply = client.complete(
            [{"role": "user", "content": QUESTION}],
            max_tokens=8, temperature=0, stream=True,
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


def probe_ladder(config, provider_name="csic", timeout=60):
    """One probe per model on the ladder, pinned model first."""
    from src.classes.AIInterpret import model_fallback
    results = []
    for model in model_fallback.candidates(config, provider_name):
        results.append(probe(dict(config, model=model), provider_name, timeout))
    return results


def verdict(results):
    """(exit status, one-line verdict) for a ladder's probe results."""
    if not results:
        return EXIT_SKIP, "no model configured"
    primary = results[0]
    if not primary.get("configured", True):
        return EXIT_SKIP, primary["error"]
    where = "%s at %s" % (primary["provider"], primary["host"] or "?")
    if primary["ok"]:
        return EXIT_OK, "%s: %s answered in %s s" % (where, primary["model"], primary["seconds"])
    answering = [r for r in results[1:] if r["ok"]]
    if answering:
        return EXIT_DEGRADED, (
            "%s: the pinned model %s is not answering (%s); %s is, so features run "
            "on it until %s recovers" % (where, primary["model"], primary["error"],
                                         answering[0]["model"], primary["model"]))
    return EXIT_FAIL, "%s: no model answered -- %s" % (
        where, "; ".join("%s: %s" % (r["model"], r["error"]) for r in results))


def _host(api_base):
    try:
        from urllib.parse import urlparse
        return urlparse(api_base).hostname or api_base
    except Exception:
        return api_base


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--timeout", type=int, default=60,
                        help="seconds for each model's call (default 60)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    name, config, enabled = resolve_provider()
    if not enabled:
        results = [{"ok": False, "configured": False, "provider": name, "host": "",
                    "model": config.get("model", ""),
                    "error": "AI_INTERPRETATION_ENABLED is false on this server"}]
        status, line = EXIT_SKIP, results[0]["error"]
    else:
        results = probe_ladder(config, name, timeout=args.timeout)
        status, line = verdict(results)

    if args.json:
        print(json.dumps({"status": status, "verdict": line, "models": results}, sort_keys=True))
        return status
    for index, r in enumerate(results):
        role = "pinned  " if index == 0 else "fallback"
        if r["ok"]:
            print("  %s %s: OK in %s s (%r)" % (role, r["model"], r["seconds"], r["reply"]))
        elif not r.get("configured", True):
            print("  %s %s: not configured" % (role, r["model"]))
        else:
            after = "" if r.get("seconds") is None else " after %s s" % r["seconds"]
            print("  %s %s: FAIL%s: %s" % (role, r["model"], after, r["error"]))
    # The verdict is the LAST line: the smoke test reads `tail -1`.
    label = {EXIT_OK: "OK", EXIT_DEGRADED: "DEGRADED", EXIT_FAIL: "FAIL", EXIT_SKIP: "SKIP"}[status]
    print("%s %s" % (label, line))
    return status


if __name__ == "__main__":
    sys.exit(main())
