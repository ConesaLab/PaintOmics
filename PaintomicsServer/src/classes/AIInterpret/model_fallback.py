"""Which model to ask when the one configured is not answering.

The pinned model comes first, always: it is pinned for reproducibility, and
a paper that names it should be describing what actually ran. But the CSIC
gateway serves several models behind one endpoint, and on 2026-09-08 the
backend for the pinned one went away ("Cannot connect to host
host.docker.internal:8000") while the gateway's own `default/llm` alias kept
answering. Every AI feature failed for the rest of the day for want of a
second name to try.

So each provider carries an ordered list of fallbacks (`fallback_models`,
from AI_<PROVIDER>_FALLBACK_MODELS; the CSIC default is the operator's
`default/llm` alias, which they keep pointed at a working model). Both
transports -- LLMClient and the Agents SDK shim -- ask the configured model
first and move down the list when a call fails in a way that says "this
model is not being served": a 5xx, a connection error, a timeout, a 404. A
4xx that would fail every model alike (auth, a bad request) is never a
reason to switch, and neither is a 429: the key is throttled, not the model.

A model that failed that way is remembered as down for COOLDOWN_SECONDS, so
the next hundred calls of an interpretation run go straight to what works
instead of each paying ten seconds to learn the same thing. When the
cooldown lapses the pinned model is tried first again -- "first DeepSeek, and
only if that is not working, the others" -- and the first answer clears it.

Process-wide state, deliberately: one worker process serves the site
(uwsgi.ini, processes = 1) and every request in it should benefit from what
the last one found out. The model that actually answered is always known
(`LLMClient.model_used`; the SDK path records the stream's `model`), so a
result can say which one wrote it.
"""
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

# How long a model that just failed is skipped before it is tried first again.
COOLDOWN_SECONDS = int(os.getenv("AI_MODEL_FALLBACK_COOLDOWN", "300"))

# Providers with a sensible fallback when nothing is configured. Only the
# CSIC gateway publishes an alias its operator keeps working.
_DEFAULT_FALLBACKS = {"csic": "default/llm"}

_down = {}                 # (api_base, model) -> time.monotonic() deadline
_lock = threading.Lock()


def _split(value):
    if isinstance(value, (list, tuple)):
        return [str(m).strip() for m in value if str(m).strip()]
    return [m.strip() for m in str(value or "").split(",") if m.strip()]


def fallback_models(provider_config, provider_name="csic"):
    """The fallbacks for this provider, in order, without the primary.

    `provider_config["fallback_models"]` wins (a list or a comma-separated
    string); a serverconf.py that predates the setting reads the environment
    variable the template would have read, then the built-in default.
    """
    configured = provider_config.get("fallback_models")
    if configured is None:
        variable = "AI_%s_FALLBACK_MODELS" % str(provider_name).replace("-", "_").upper()
        configured = os.getenv(variable)
        if configured is None:
            configured = _DEFAULT_FALLBACKS.get(provider_name, "")
    primary = provider_config.get("model", "")
    out = []
    for model in _split(configured):
        if model != primary and model not in out:
            out.append(model)
    return out


def candidates(provider_config, provider_name="csic"):
    """Primary first, then the fallbacks. Never empty."""
    return [provider_config.get("model", "")] + fallback_models(provider_config, provider_name)


def is_down(api_base, model):
    with _lock:
        until = _down.get((api_base, model))
    return until is not None and time.monotonic() < until


def mark_down(api_base, model, reason=""):
    """Remember that `model` is not being served, for COOLDOWN_SECONDS."""
    fresh = not is_down(api_base, model)
    with _lock:
        _down[(api_base, model)] = time.monotonic() + COOLDOWN_SECONDS
    if fresh:
        logger.warning("LLM model %s at %s is not answering (%s); skipping it for "
                       "the next %d s", model, api_base, str(reason)[:200] or "no detail",
                       COOLDOWN_SECONDS)


def mark_up(api_base, model):
    """An answer arrived: the model is served again."""
    with _lock:
        was_down = _down.pop((api_base, model), None) is not None
    if was_down:
        logger.info("LLM model %s at %s answered; back in use", model, api_base)


def ordered(api_base, models):
    """The models to try, in order: the ones believed up, then the rest.

    A ladder whose every rung is in cooldown still tries all of them, in the
    configured order -- there is nothing better to do, and one of them may be
    back.
    """
    up = [m for m in models if not is_down(api_base, m)]
    down = [m for m in models if is_down(api_base, m)]
    return up + down


def falls_back(exc):
    """Whether this failure means "try another model" rather than "give up".

    requests-based classification, for LLMClient. The SDK shim has its own
    (`_transient` in agent.py) and excludes 429 the same way.
    """
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status is None:
            return True
        return status >= 500 or status == 404
    return False


def reset():
    """Forget every cooldown (tests)."""
    with _lock:
        _down.clear()
