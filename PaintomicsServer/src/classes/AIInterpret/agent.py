"""The OpenAI Agents SDK runtime the graph walk's model loops run on.

The walker (``walker/sdk.py``) and the Writer (``walker/writer.py``) are agent
loops on the SDK. This module points the SDK at the configured gateway once per
process and wraps its transport: every completion is issued as a stream and
folded back, calls are paced, transient gateway errors are retried, a model
that is not being served falls back to the next one, and a run's deadline
(``set_run_deadline``) stops retries that could not finish in time.
"""
import asyncio
import contextvars
import logging
import os
import threading
import time

from agents import (
    OpenAIChatCompletionsModel, set_default_openai_api, set_default_openai_client,
    set_tracing_disabled,
)
import httpx
# openai 3.x moved its HTTP transport off `httpx` onto `httpx2`, a SEPARATE
# distribution whose exception tree is unrelated: issubclass(httpx2.HTTPError,
# httpx.HTTPError) is False. openai wraps only the INITIAL request, so a
# connection dropped -- or a read that times out between tokens -- while
# ITERATING a completion stream still surfaces as a raw transport error, and
# that is the case the retry shim below exists for. Catching `httpx.HTTPError`
# alone leaves the shim dead on openai 3.x, silently: an isinstance() that
# stops matching logs nothing, so a fault that used to cost one 2 s retry ends
# the whole run instead. Bind the module once here, and list both names, so
# this works on either SDK generation and the three uses cannot drift apart.
try:                                    # openai >= 3
    import httpx2 as _transport
except ImportError:                     # openai 2.x
    _transport = httpx
_TRANSPORT_ERRORS = ((httpx.HTTPError,) if _transport is httpx
                     else (httpx.HTTPError, _transport.HTTPError))
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from src.classes.AIInterpret import model_fallback
from src.conf.serverconf import AI_LLM_PROVIDER, AI_PROVIDERS

logger = logging.getLogger(__name__)

# Every chat completion is issued as a stream and folded back into a
# ChatCompletion for the SDK (see _stream_to_completion). Measured on the CSIC
# gateway 2026-08-17: a non-streamed generation has ~120 s per attempt before
# the gateway abandons it (14k tokens: HTTP 200 after 477 s = four attempts;
# the citation top-up of a 105-pathway report: HTTP 408 after 489 s, three
# times over), while a streamed 25k-token generation completed in 203 s --
# the budget is per read, not per response. Set AI_SDK_STREAM=0 only for a
# provider that cannot stream chat completions.
SDK_STREAM = os.getenv("AI_SDK_STREAM", "1") != "0"
# httpx read timeout, per read: with streaming that is "no token for this
# long", not "the whole answer in this long". Generous, because a large prompt
# queues behind other work before its first token; a stalled stream still
# surfaces as APITimeoutError and gets the shim's retry.
SDK_HTTP_READ_TIMEOUT = float(os.getenv("AI_SDK_HTTP_READ_TIMEOUT", "180"))
# The run's own deadline, visible to the transport. Every phase-level guard in
# this file bounds the work it starts, but the retry shim underneath them had a
# budget of its own -- 4 attempts x a 180 s read timeout, 8 when throttled --
# and no idea when the run was due. A single Lead call spent 1604 s that way,
# without issuing one tool call, and the run finished at 1722 s against a 600 s
# ceiling. A ContextVar because it is per-run and inherited by every task the
# run spawns, where a module global would leak between concurrent jobs.
_RUN_DEADLINE = contextvars.ContextVar("ai_run_deadline", default=None)
# Below this there is no point starting another attempt: it cannot return
# anything the caller will still be alive to use.
RETRY_MIN_ATTEMPT_SECONDS = float(os.getenv("AI_RETRY_MIN_ATTEMPT", "20"))


def set_run_deadline(when):
    """Arm the transport's deadline for this run. `when` is an epoch time."""
    _RUN_DEADLINE.set(when)


def _run_seconds_left():
    """Seconds until the run is due, or None when no deadline is armed."""
    when = _RUN_DEADLINE.get()
    return None if when is None else when - time.time()

_sdk_configured = False
# The configured client, kept reachable so the retry shim can be driven in a
# test: its worst case is longer than a whole run, so it needs one.
_CLIENT = None
_MODEL_OBJ = None


class _AsyncPacer:
    """Space gateway calls to AI_LLM_MAX_RPM requests/min (0 disables).

    The SDK drives its own AsyncOpenAI client, so LLMClient's token bucket --
    the thing that took the live arm from 6 lost sub-agents to 0 -- never sees
    these calls. Without this shim an SDK run is unpaced against a gateway
    with a measured ceiling of 60/min, and its scores are not comparable to a
    paced live run (round 1p: same agent, pacing alone moved the gap +0.27).

    The pace is one process-wide schedule: every walk owns a fresh event loop
    on its own queue worker thread, all against the same gateway. The reservation below has
    no await inside it, so a plain threading.Lock guards it correctly across
    loops and threads alike -- an asyncio.Lock would bind to one loop and
    either leak per job or trip over a reused loop id.
    """

    def __init__(self, rpm):
        self._interval = 60.0 / rpm if rpm > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    async def wait(self):
        if not self._interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)


class _EmptyStream(Exception):
    """The gateway closed a completion stream without a single choice."""


# Lengths of answers the model cut off at its token limit, this process: the
# log says which call ran out, this says how many did.
_TRUNCATIONS = []


async def _stream_to_completion(stream):
    """Fold a chat-completion chunk stream into one ChatCompletion.

    The SDK's non-streaming path calls ``create(stream=False)`` and reads a
    ``ChatCompletion``. We issue the request as a stream instead (see
    SDK_STREAM for why) and rebuild the object it expects: content deltas
    concatenate, tool-call deltas join by ``index`` (name and id arrive in the
    first fragment, arguments trickle in over the rest), the last non-null
    ``finish_reason`` wins, and ``usage`` is whatever the final chunk carried
    (vLLM sends it only when ``stream_options.include_usage`` is set).

    Provider-specific delta fields the SDK does not read (DeepSeek's
    ``reasoning_content``, for one) are dropped here on purpose.
    """
    content = []
    role = None
    refusal = []
    tool_calls = {}          # index -> {"id", "type", "name", "arguments"}
    finish_reason = None
    usage = None
    meta = {}
    saw_choice = False
    try:
        async for chunk in stream:
            if not meta:
                meta = {"id": chunk.id, "created": chunk.created,
                        "model": chunk.model,
                        "system_fingerprint": getattr(chunk, "system_fingerprint", None)}
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            for choice in (chunk.choices or []):
                saw_choice = True
                delta = choice.delta
                if delta is None:
                    continue
                if delta.role:
                    role = delta.role
                if delta.content:
                    content.append(delta.content)
                if getattr(delta, "refusal", None):
                    refusal.append(delta.refusal)
                for tc in (delta.tool_calls or []):
                    slot = tool_calls.setdefault(tc.index, {
                        "id": None, "type": "function", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.type:
                        slot["type"] = tc.type
                    fn = tc.function
                    if fn is not None:
                        if fn.name:
                            slot["name"] += fn.name
                        if fn.arguments:
                            slot["arguments"] += fn.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
    if not saw_choice:
        raise _EmptyStream("completion stream ended without any choice")

    message = {"role": role or "assistant",
               "content": "".join(content) if content else None,
               "refusal": "".join(refusal) if refusal else None}
    if tool_calls:
        message["tool_calls"] = [
            {"id": slot["id"] or "call_%d" % idx, "type": slot["type"] or "function",
             "function": {"name": slot["name"], "arguments": slot["arguments"]}}
            for idx, slot in sorted(tool_calls.items())]
    if finish_reason is None:
        # vLLM always closes with one; a provider that does not has still
        # answered, so record the answer rather than discard it.
        finish_reason = "tool_calls" if tool_calls else "stop"
        logger.warning("completion stream ended without finish_reason; assuming %s",
                       finish_reason)
    elif finish_reason == "length":
        # The model ran out of output budget mid-sentence and the partial text
        # ships as if it were finished. That is how a stored report ends on the
        # bare heading "### 4." with no Limitations section after it: the last
        # section is simply where the tokens ran out, and nothing anywhere said
        # so. Counted so a truncated interpretation can be told from a short one.
        _TRUNCATIONS.append(len("".join(content)))
        logger.warning("[AI] completion TRUNCATED at the token limit after %d "
                       "characters; the tail of this answer is missing",
                       len("".join(content)))
    payload = {
        "id": meta.get("id") or "chatcmpl-stream",
        "object": "chat.completion",
        "created": meta.get("created") or int(time.time()),
        "model": meta.get("model") or "",
        "system_fingerprint": meta.get("system_fingerprint"),
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": message, "logprobs": None}],
        "usage": usage.model_dump() if usage is not None else None,
    }
    return ChatCompletion.model_validate(payload)


def configure_sdk():
    """Point the SDK at our OpenAI-compatible gateway. Idempotent."""
    global _sdk_configured, _MODEL_OBJ, _CLIENT
    if _sdk_configured:
        return
    provider = AI_PROVIDERS[AI_LLM_PROVIDER]
    # max_retries=0: the shim below is the ONE retry policy. The client's own
    # default (2) stacked under the shim's 4 attempts turned a single 408 --
    # eight minutes each on this gateway -- into twelve of them, and that is
    # how two live runs spent 90+ minutes inside one citation top-up.
    # The read timeout is per read: with streaming (SDK_STREAM) it bounds the
    # silence between tokens, and a stalled request surfaces as
    # APITimeoutError instead of holding the phase open.
    client = _CLIENT = AsyncOpenAI(
        base_url=provider["api_base"], api_key=provider["api_key"],
        max_retries=0,
        timeout=_transport.Timeout(connect=30.0, read=SDK_HTTP_READ_TIMEOUT,
                                   write=60.0, pool=30.0))

    # Honour the same pacing knob as LLMClient. Applied at the transport
    # method the SDK actually calls, so every agent turn and tool round-trip
    # is spaced -- not just the calls this module makes directly.
    rpm = int(os.getenv("AI_LLM_MAX_RPM", "0") or 0)
    pacer = _AsyncPacer(rpm)
    completions = client.chat.completions
    completions._pa_orig_create = completions.create

    # One shim for pacing AND resilience: the CSIC gateway answers with bare
    # 500s ("connection reset by peer" from the vLLM behind litellm) during
    # load spikes, and a transient 500 must cost a retry, not the whole run --
    # LLMClient already behaves this way; the SDK transport has to match.
    import openai as _oai

    # 408 Request Timeout arrives as a bare APIStatusError (no dedicated
    # subclass); the gateway answers with it when a long tool-loop turn
    # outlives its upstream budget, and it is exactly as transient as a 5xx.
    _RETRY_STATUSES = {408, 409}

    def _transient(e):
        if isinstance(e, (_oai.InternalServerError, _oai.APIConnectionError,
                          _oai.APITimeoutError, _oai.RateLimitError)):
            return True
        if isinstance(e, _TRANSPORT_ERRORS + (_EmptyStream,)):
            # Raised while *iterating* a stream (openai wraps only the initial
            # request): a dropped connection, a read timeout between tokens,
            # or a stream that closed empty. All as transient as a 5xx.
            return True
        return (isinstance(e, _oai.APIStatusError)
                and getattr(e, "status_code", None) in _RETRY_STATUSES)

    _ATTEMPTS = 4  # one call plus three retries
    # 429 has a policy of its own. A throttled request answers instantly and
    # costs nothing to retry, where a 408 already cost minutes on the wire; and
    # with the client's own retries off (max_retries=0) this shim is all that
    # stands between a 60 rpm key and a verification phase that fans out 28
    # citations at once. Four quick tries there redacted citations the gateway
    # had merely throttled (live run 11n0VMC305). Retry-After is honoured when
    # the gateway sends it; otherwise the wait grows to a 30 s cap.
    _RATE_ATTEMPTS = 8

    def _retry_after(e):
        response = getattr(e, "response", None)
        headers = getattr(response, "headers", None)
        value = headers.get("retry-after") if headers else None
        try:
            return float(value) if value else None
        except (TypeError, ValueError):
            return None

    async def _issue(*args, **kwargs):
        # A caller that streams for itself (Runner.run_streamed) gets the raw
        # stream; every other call is issued as a stream and folded, because
        # on this gateway a non-streamed answer has ~120 s per attempt and a
        # streamed one has that per token.
        orig = completions._pa_orig_create
        if kwargs.get("stream") is True or not SDK_STREAM:
            return await orig(*args, **kwargs)
        kwargs = dict(kwargs, stream=True,
                      stream_options={"include_usage": True})
        stream = await orig(*args, **kwargs)
        return await _stream_to_completion(stream)

    # The model ladder (see model_fallback). The SDK passes the model by
    # keyword on every create, so routing around a model that is not being
    # served is one kwarg -- and the completion carries the model that
    # answered, so the report says which one wrote it.
    api_base = provider["api_base"]
    ladder = model_fallback.candidates(provider, AI_LLM_PROVIDER)

    def _route(kwargs):
        """A model in cooldown is replaced before the call goes out."""
        asked = kwargs.get("model")
        if asked not in ladder:
            return kwargs
        head = model_fallback.ordered(api_base, ladder)[0]
        return kwargs if head == asked else dict(kwargs, model=head)

    async def _paced_create(*args, **kwargs):
        attempt = 0
        while True:
            await pacer.wait()
            sent = _route(kwargs)
            try:
                result = await _issue(*args, **sent)
                model_fallback.mark_up(api_base, sent.get("model"))
                return result
            except asyncio.CancelledError:
                # Never retry a cancellation. httpx maps some cancellations
                # during a stream read onto its own error types, and the
                # transient test below would answer True for those -- turning
                # "the run is over" into "sleep, then try again".
                raise
            except (_oai.APIError, *_TRANSPORT_ERRORS, _EmptyStream) as e:
                if not _transient(e):
                    raise
                attempt += 1
                throttled = isinstance(e, _oai.RateLimitError)
                limit = _RATE_ATTEMPTS if throttled else _ATTEMPTS
                status = getattr(e, "status_code", None)
                # A model that is not being served: the next rung, now, with
                # no wait -- a different model is not a retry of the same one.
                # Throttling is the key's, not the model's, so 429 stays put.
                if not throttled and sent.get("model") in ladder and attempt < limit:
                    model_fallback.mark_down(api_base, sent.get("model"), e)
                    alternatives = [m for m in ladder
                                    if not model_fallback.is_down(api_base, m)]
                    if alternatives:
                        logger.warning("SDK transport switching model %s -> %s after %s%s",
                                       sent.get("model"), alternatives[0],
                                       type(e).__name__, " %s" % status if status else "")
                        kwargs = dict(kwargs, model=alternatives[0])
                        continue
                if attempt >= limit:
                    logger.warning("SDK transport giving up after %d attempts (%s%s)",
                                   attempt, type(e).__name__,
                                   " %s" % status if status else "")
                    raise
                if throttled:
                    delay = _retry_after(e) or min(3.0 * 2 ** (attempt - 1), 30.0)
                else:
                    delay = min(2 ** (attempt - 1) * 2, 15)
                left = _run_seconds_left()
                if left is not None and left < delay + RETRY_MIN_ATTEMPT_SECONDS:
                    logger.warning("SDK transport stopping after %d attempt(s) "
                                   "(%s%s): %.0fs left of the run, an attempt "
                                   "needs %.0fs", attempt, type(e).__name__,
                                   " %s" % status if status else "", left,
                                   delay + RETRY_MIN_ATTEMPT_SECONDS)
                    raise
                logger.warning("SDK transport retry %d/%d after %s%s (waiting %.0fs)",
                               attempt, limit - 1, type(e).__name__,
                               " %s" % status if status else "", delay)
                await asyncio.sleep(delay)

    # The original stays reachable as _pa_orig_create: tests substitute it,
    # and a provider that cannot stream is pointed back at it by AI_SDK_STREAM=0.
    completions.create = _paced_create
    logger.info("Agents SDK transport armed: pacing %s rpm, streaming %s, "
                "retry x%d on 5xx/timeouts and x%d on 429, read timeout %ss",
                rpm or "off", "on" if SDK_STREAM else "off",
                _ATTEMPTS - 1, _RATE_ATTEMPTS - 1, SDK_HTTP_READ_TIMEOUT)
    # chat_completions, not the Responses API: the CSIC gateway is vLLM, which
    # speaks /chat/completions only.
    set_default_openai_api("chat_completions")
    set_default_openai_client(client)
    set_tracing_disabled(True)  # never ship job data to OpenAI's trace backend

    # Passing the model as a *string* routes through the SDK's MultiProvider,
    # which splits on "/" and reads the left side as a provider prefix. Every
    # CSIC model id contains a slash ("deepseek-ai/DeepSeek-V4-Flash-0731"), so
    # that path dies with UserError: Unknown prefix: deepseek-ai. Handing the
    # SDK a concrete model object bypasses provider resolution entirely.
    _MODEL_OBJ = OpenAIChatCompletionsModel(model=provider["model"],
                                            openai_client=client)
    _sdk_configured = True
    logger.info("Agents SDK configured: provider=%s model=%s fallbacks=%s",
                AI_LLM_PROVIDER, provider["model"], ladder[1:] or "none")


def _model():
    if _MODEL_OBJ is None:
        configure_sdk()
    return _MODEL_OBJ
