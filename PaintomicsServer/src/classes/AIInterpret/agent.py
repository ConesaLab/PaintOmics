"""The AI interpretation agent workflow, built on the OpenAI Agents SDK.

This is the production entry point for AI interpretation: the SDK's ``Runner``
drives the agent loop and tool round-trips, and structured phases declare a
pydantic ``output_type`` instead of parsing free text. Everything that is not
orchestration lives in sibling modules and is shared by import:

  * prompts (``prompts.py``), tool bodies (``tools.py``)
  * literature retrieval (``pubmed_client.py``)
  * context builders (``context_builder.py``)
  * cross-pipeline helpers (``shared.py``)
  * budgets from ``serverconf.py``

The SDK is async-first while PaintOmics runs on PySiQ's threads, so the public
entry points stay synchronous and own their own event loop:

  * ``run_ai_agent(job_id, experiment_design, RESPONSE)`` -- servlet-facing
    adapter (DAO progress, persistence, cancellation, concurrency semaphore).
  * ``run_agent_loop_workflow`` (in ``agent_loop.py``) -- the bare interpreter,
    and the only arm. The fixed six-phase workflow that lived here was removed.
"""
import asyncio
import contextvars
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from agents import (
    Agent, ModelSettings, OpenAIChatCompletionsModel, RunContextWrapper, Runner,
    function_tool, set_default_openai_api, set_default_openai_client,
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
from pydantic import BaseModel, Field

from src.classes.AIInterpret import model_fallback
from src.conf.serverconf import (
    AI_LLM_PROVIDER,
    AI_PROVIDERS,
    AI_TEMPERATURE,
    AI_SEARCH_SUBAGENT_WORKERS,
    # Read through this module by the agent-loop end-to-end test
    # (agent.AI_MAX_PATHWAYS etc.), not used here.
    AI_MAX_PATHWAYS,  # noqa: F401 -- read and patched as agent.AI_MAX_PATHWAYS by the e2e tests
    AI_MAX_SEARCH_TASKS,  # noqa: F401 -- read and patched as agent.AI_MAX_SEARCH_TASKS by the e2e tests
    AI_PAPERS_PER_SEARCH_TASK,  # noqa: F401 -- read and patched as agent.AI_PAPERS_PER_SEARCH_TASK by the e2e tests
)
from src.classes.AIInterpret import tools as tools_mod
from src.classes.AIInterpret import prompts as prompts_mod

logger = logging.getLogger(__name__)

# How many pathways one cluster-mode interpretation batch may hold (units are
# never split; a bigger cluster travels alone) and how many batches run at
# once. Cluster mode has ~5x the batches of the top-15 path, so it needs a
# bound the three-batch path never did.
CLUSTER_BATCH_MAX = int(os.getenv("AI_CLUSTER_BATCH_MAX", "8"))
CLUSTER_CONCURRENCY = int(os.getenv("AI_CLUSTER_CONCURRENCY", "6"))
# Two tiers of interpretation in cluster mode. A unit that carries a top-N
# (by p-value) pathway gets the full tool-loop interpreter, capped at
# CLUSTER_INTERPRET_TURNS; every other unit gets one single-shot
# call from the same instructions without tools. Measured: a full-protocol
# cluster run at 8 turns x 14 batches blew a 600 s cap on the gateway, where
# parallel tool loops serialise; single-shot calls parallelise.
CLUSTER_INTERPRET_TURNS = int(os.getenv("AI_CLUSTER_INTERPRET_TURNS", "5"))
# AI_CLUSTER_TOOLS=0 makes every cluster batch single-shot (no data tools) --
# the cheap mode for gateways whose tool loops crawl, and the mode a
# qualitative smoke can run on a backend that never finishes a tool loop.
CLUSTER_TOOLS = os.getenv("AI_CLUSTER_TOOLS", "1") == "1"

# Tuning knobs for the SDK arm. Separate from the shared AI_* settings so
# sweeping this workflow keeps its behaviour stable mid-comparison.
SDK_VERIFY_CONCURRENCY = int(os.getenv("AI_SDK_VERIFY_CONCURRENCY", "8"))
SDK_SEARCH_CONCURRENCY = int(os.getenv("AI_SDK_SEARCH_CONCURRENCY",
                                        str(AI_SEARCH_SUBAGENT_WORKERS)))
# Search every triaged pathway rather than only the planner's task list: PubMed
# retrieval is 6-10s of a 300s budget, so breadth here is nearly free, and
# citations are the metric we are short on.
SDK_SEARCH_ALL_PATHWAYS = os.getenv("AI_SDK_SEARCH_ALL_PATHWAYS", "1") == "1"
# Ceiling on the total search-task list once per-pathway backfill is added
# (planner tasks + up to three angles per uncovered pathway). Unbounded, a
# 40-pathway triage would issue 120+ PubMed queries and their screening calls.
SDK_BACKFILL_MAX_TASKS = int(os.getenv("AI_SDK_BACKFILL_MAX_TASKS", "45"))
# Papers shown to one interpretation batch. More retrieved literature is good;
# more literature *per prompt* is not -- see the cap in _one_batch.
SDK_PAPERS_PER_BATCH = int(os.getenv("AI_SDK_PAPERS_PER_BATCH", "10"))
# Tool-loop depth for an interpretation batch. Batches run in parallel, so this
# sets the slowest-batch floor on the interpretation phase -- 109s of a 318s
# run. Worth bounding: measured batch reports carry no citations at all, so
# extra turns buy analysis depth rather than references.
SDK_INTERPRET_TURNS = int(os.getenv("AI_SDK_INTERPRET_TURNS", "8"))
# Straggler cutoff. Median call is ~3.5s and a stalled one runs ~63s, so
# anything past this is stuck rather than slow -- see run_hedged.
SDK_CALL_TIMEOUT = float(os.getenv("AI_SDK_CALL_TIMEOUT", "45"))
# Below this many cited papers, synthesis is asked once more to use what it was
# given. Not a quota: the quote and verification guards still decide what stays.
SDK_MIN_CITATIONS = int(os.getenv("AI_SDK_MIN_CITATIONS", "22"))
# Parallel synthesis drafts, best kept. Defaults to 1 -- best-of-N was tried and
# does not pay here, for two measured reasons:
#
#   * It is not free in wall-clock. Short calls parallelise on this gateway (32
#     concurrent in 5.6s), but three full-length syntheses took 216s against ~80s
#     for one, pushing a 280s run to 417s. Long generations queue.
#   * The drafts barely differ on anything selectable from the data: scores came
#     out 162 / 164 / 164, so the selector was choosing between near-identical
#     candidates and the variance that motivated this lives elsewhere.
#
# Raise it only where wall-clock is not a constraint.
SDK_SYNTH_DRAFTS = int(os.getenv("AI_SDK_SYNTH_DRAFTS", "1"))
# Deterministic completeness pass. Off by default -- see the note at its call
# site: it works, and it costs ~190s for a quality change that measured negative.
SDK_GAP_FILL = os.getenv("AI_SDK_GAP_FILL", "0") == "1"
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
# Wall-clock cap on one long-form call (synthesis draft, gap-fill, top-up,
# correction rewrite). These echo or write a whole report, so they cannot use
# run_hedged's 45 s straggler cutoff -- but nor may they run unbounded: two
# live runs sat 90+ minutes inside one top-up before this existed.
SDK_LONG_CALL_TIMEOUT = float(os.getenv("AI_SDK_LONG_CALL_TIMEOUT", "600"))
# The run's own deadline, visible to the transport. Every phase-level guard in
# this file bounds the work it starts, but the retry shim underneath them had a
# budget of its own -- 4 attempts x a 180 s read timeout, 8 when throttled --
# and no idea when the run was due. A single Lead call spent 1604 s that way,
# without issuing one tool call, and the run finished at 1722 s against a 600 s
# ceiling. A ContextVar because it is per-run and inherited by every task the
# run spawns, where a module global would leak between concurrent jobs.
_RUN_DEADLINE = contextvars.ContextVar("ai_run_deadline", default=None)
# Gateway weather, per run. Round 34 logged ONE transport rate-limit retry
# across eight replicates; round 35 logged sixteen. That is a swing large
# enough to move failure counts and wall times on its own, and it lived only in
# the log -- so every archived comparison silently assumed the two rounds met
# the same gateway. A ContextVar for the same reason the deadline is one: it is
# inherited by child tasks, where a module global would bleed between
# concurrent jobs.
_RUN_RETRIES = contextvars.ContextVar("ai_run_retries", default=None)
# Below this there is no point starting another attempt: it cannot return
# anything the caller will still be alive to use.
RETRY_MIN_ATTEMPT_SECONDS = float(os.getenv("AI_RETRY_MIN_ATTEMPT", "20"))


def set_run_deadline(when):
    """Arm the transport's deadline for this run. `when` is an epoch time."""
    _RUN_DEADLINE.set(when)


def reset_run_retries():
    """Start a run's retry tally. Call once, where the deadline is set."""
    _RUN_RETRIES.set({"transport": 0, "rate_limited": 0})


def run_retry_counts():
    """The tally, or an empty dict if nothing armed one."""
    return dict(_RUN_RETRIES.get() or {})


def _count_retry(exc):
    tally = _RUN_RETRIES.get()
    if tally is None:
        return
    tally["transport"] = tally.get("transport", 0) + 1
    if "ratelimit" in type(exc).__name__.lower():
        tally["rate_limited"] = tally.get("rate_limited", 0) + 1


def _run_seconds_left():
    """Seconds until the run is due, or None when no deadline is armed."""
    when = _RUN_DEADLINE.get()
    return None if when is None else when - time.time()
# The whole interpretation, end to end. A run that is still going after this
# has stalled somewhere the phase caps did not reach; better an error the user
# can retry than a job that reads as running for the rest of the day.
AI_MAX_RUN_SECONDS = float(os.getenv("AI_MAX_RUN_SECONDS", "2700"))
# What the pipeline must keep in hand after the verify loop: quote collection
# (capped at AI_QUOTE_DEADLINE, 45 s), reference rendering, the programmatic
# redaction net and the save. Measured at 0.0-0.1 s for the net itself, so this
# is dominated by the quote pass.
VERIFY_TAIL_RESERVE = float(os.getenv("AI_VERIFY_TAIL_RESERVE", "60"))
# One verification fan-out, before any has been measured in this run.
VERIFY_ITER_RESERVE = float(os.getenv("AI_VERIFY_ITER_RESERVE", "90"))
# A correction rewrite is a full-report synthesis echo (~70 s of a 347 s run).
VERIFY_REWRITE_RESERVE = float(os.getenv("AI_VERIFY_REWRITE_RESERVE", "90"))

_SYSTEM_STOPWORDS = frozenset("""
a an the and or of in on at to for from with without by as is are was were be
been being this that these those it its their there here into over under
between across per via using used use data dataset datasets sample samples
study analysis experiment experimental design condition conditions time course
timecourse series omics omic multi multiomics layer layers level levels five
four three two one several multiple various different profile profiles
""".split())




# Servlet-facing state: the servlet flips
# _cancel_flags[job_id] to cancel, and the semaphore bounds concurrent runs.
import threading
from src.conf.serverconf import AI_MAX_CONCURRENT_PIPELINES
_cancel_flags = {}
_agent_semaphore = threading.Semaphore(AI_MAX_CONCURRENT_PIPELINES)

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

    The pace is one process-wide schedule: the interpreter owns a fresh
    event loop per job and up to AI_MAX_CONCURRENT_PIPELINES jobs run on
    separate threads, all against the same gateway. The reservation below has
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



# Lengths of answers the model cut off at its token limit, this process.
# Read by run_ai_agent to stamp `truncated_calls` on the stored record.
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


async def bounded(awaitable, timeout, label=""):
    """``asyncio.wait_for`` with the reason on the record.

    Every long-form model call goes through here so a stall has a ceiling: the
    task is cancelled at ``timeout`` and asyncio.TimeoutError propagates to a
    caller that decides whether the phase was optional (skip it) or not (fail
    the run). Logged at WARNING because a timeout here is always news.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("[bounded] %s exceeded %ss and was cancelled",
                       label or "call", timeout)
        raise


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
                        _count_retry(e)
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
                _count_retry(e)
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


_CAVEAT_CUES = (
    r"did not reach|short of significance|non-?significant|marginal|\btrend\b",
    r"discordan|does not follow|without corresponding|inconsist",
    r"annotation artefact|annotation artifact|named after|label reflects",
    r"driven (?:almost )?(?:entirely|solely) by|single (?:omic )?layer|only .{0,20}assay",
    r"remains? to be|requires? (?:further )?(?:testing|validation)|hypothes",
    r"control point|rate-?limiting|bottleneck",
    r"both up.{0,15}and down|mixed direction|in opposite direction",
)








async def run_hedged(agent, prompt, ctx, max_turns=6, timeout=None, label=""):
    """Runner.run, with a straggler retried instead of waited on.

    Measured on the CSIC gateway: fire 16 identical requests and the median
    returns in 3.5s while ONE takes 63s -- reproducibly, roughly one trial in
    three. Because ``asyncio.gather`` waits for the slowest, that single
    straggler sets the whole phase's wall-clock: it is why the interpretation
    phase sat at ~107s regardless of batch count or tool-turn budget.

    A stalled request is not making progress, so waiting on it buys nothing.
    Cancel it, issue a fresh one, and keep whichever answers. Cost is one extra
    request on a small fraction of calls; the saving is tens of seconds of
    wall-clock per phase.
    """
    if timeout is None:
        timeout = SDK_CALL_TIMEOUT
    for attempt in (1, 2):
        try:
            return await asyncio.wait_for(
                Runner.run(agent, prompt, context=ctx, max_turns=max_turns),
                timeout=timeout)
        except asyncio.TimeoutError:
            if attempt == 1:
                logger.warning("[hedge] %s exceeded %ss; cancelling and retrying",
                               label or agent.name, timeout)
                continue
            logger.warning("[hedge] %s timed out twice; giving up",
                           label or agent.name)
            raise


# ---------------------------------------------------------------------------
# Shared context + structured output types
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """Threaded through every agent and tool via RunContextWrapper."""
    job_instance: Any
    job_id: str
    organism_name: str
    experiment_design: str
    paper_index: dict = field(default_factory=dict)   # ref_index -> paper
    llm: Any = None                                    # for extract_evidence
    tool_calls: int = 0                                # instrumentation










class RelevantPMIDs(BaseModel):
    """Replaces _parse_pmid_list."""
    pmids: list[str]


class Verdict(BaseModel):
    """Replaces _parse_json_verdict."""
    supported: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str








@function_tool
def search_paper_text(ctx: RunContextWrapper[AgentContext], ref_index: int, query: str) -> str:
    """Search the full text of a cited paper for a phrase. ref_index is the [n] citation number."""
    ctx.context.tool_calls += 1
    executor = tools_mod.build_verification_executor(ctx.context.paper_index)
    return executor("search_paper_text", {"ref_index": ref_index, "query": query})


@function_tool
def fetch_paper_section(ctx: RunContextWrapper[AgentContext], ref_index: int, section: str) -> str:
    """Fetch a named section (abstract, results, discussion) of a cited paper."""
    ctx.context.tool_calls += 1
    executor = tools_mod.build_verification_executor(ctx.context.paper_index)
    return executor("fetch_paper_section", {"ref_index": ref_index, "section": section})






VERIFY_TOOLS = [search_paper_text, fetch_paper_section]


# ---------------------------------------------------------------------------
# Agents. Instructions come verbatim from prompts.py -- see fairness contract.
# ---------------------------------------------------------------------------

def _build_agents():
    ms = ModelSettings(temperature=AI_TEMPERATURE)
    strict = ModelSettings(temperature=0.1)






    synthesizer = Agent[AgentContext](
        name="Report Writer",
        model=_model(),
        instructions=prompts_mod.SYSTEM_PROMPT_SYNTHESIZE,
        model_settings=ms,
        tools=[],
    )

    # DANGER, measured 2026-08-07 against the CSIC gateway: declaring BOTH
    # tools= and output_type= on one Agent produces a verifier that never
    # verifies. output_type compiles to response_format, and vLLM's grammar
    # constrains the first token to "{", so the model cannot emit a tool call.
    # In the A/B (scratchpad/test_sdk_tools_vs_schema.py) the schema-typed
    # verifier made 0 tool calls and still returned supports_claim=true, its
    # reasoning field narrating the check it had not performed. The identical
    # agent without output_type made 3 tool calls and quoted the paper.
    #
    # A citation verifier that rubber-stamps is worse than none: it converts
    # "unchecked" into "checked and passed". So tools win and the verdict is
    # parsed from text -- the same trade llm_client.complete_with_tools_json
    # makes by coercing only after the tool loop has finished.
    verifier = Agent[AgentContext](
        name="Claim Verifier",
        model=_model(),
        instructions=prompts_mod.SYSTEM_PROMPT_VERIFICATION,
        model_settings=strict,
        tools=VERIFY_TOOLS,
    )
    # The prefetch path's verifier: same instructions, NO tools. Porting the
    # prompt alone was not enough -- measured on a smoke run, prefetch with the
    # tool-carrying verifier still produced "Max turns (2) exceeded" four times,
    # because the model can still choose to call a tool and now has two turns to
    # exhaust instead of six. It failed FASTER. The agent arm's version has
    # tools=[] for this reason and says so: "No tools, one call, no turns to
    # exhaust."
    verifier_solo = Agent[AgentContext](
        name="Claim Verifier (prefetched)",
        model=_model(),
        instructions=prompts_mod.SYSTEM_PROMPT_VERIFICATION,
        model_settings=strict,
        tools=[],
    )
    # Only the two the interpreter loop drives. The six built for the
    # six-phase workflow went with it.
    return dict(synth=synthesizer, verify=verifier, verify_solo=verifier_solo)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

SENTENCE_REPAIR = os.getenv("AI_SENTENCE_REPAIR", "0") == "1"
VERIFY_MEMO = os.getenv("AI_VERIFY_MEMO", "0") == "1"
# Hand the verifier its evidence instead of making it hunt for it -- ported from
# the agent arm, where it has been the default for many rounds on this measured
# result: 29 of 29 calls returned a verdict at a median 2 464 ms, redactions fell
# 12 -> 2, the verify loop 291 s -> 117 s, the run 485 s -> 338 s.
#
# The comment that shipped with it noted "the same warning appears in the
# workflow arm's logs" and nobody acted on it. Counted since: 53 "Max turns (6)
# exceeded" verifier failures across rounds 34-36, ALL of them in this arm and
# NONE in the agent arm -- about five per base run. A verifier that raises counts
# as a failure, so each one redacts a citation for a tooling reason rather than a
# grounding one, which is most of why this arm redacts 10 sentences a run to the
# agent arm's 5.75.
#
# Finding the quote is mechanical and tools.py does it in pure Python, so the
# passage is extracted in code and pasted into the prompt. The model is left with
# the one judgement only it can make. Off by default for one measuring round.
# DEFAULT ON as of round 37, on the measured result: over 4 replicates against
# base-v35's rewrite path, verifier deaths went ~5/run -> 0, redactions 10.0 ->
# 3.0, the verify loop 259.5 s -> 135.8 s, wall 418.7 s -> 298.4 s, and gateway
# rate-limit retries 10.0/run -> 0.0. The verify loop also began CONVERGING --
# 15 failed -> 2 -> 0 -- instead of exiting on no progress.
# Citations came in at 20.25 against 22.8, so the one prediction that did not
# hold was "citations hold or rise"; the drop sits inside this arm's own
# round-to-round range (22.8, 17.8) and is recorded rather than explained away.
# AI_VERIFY_PREFETCH=0 restores the tool-loop verifier for comparison.
VERIFY_PREFETCH = (os.getenv("AI_VERIFY_PREFETCH") or "1").strip().lower() \
    not in ("0", "false", "no")




SENTENCE_REPAIR_WORKERS = int(os.getenv("AI_SENTENCE_REPAIR_WORKERS", "6"))


async def _repair_sentences(agent, ctx, report, failed, job_id, stats, timeout):
    """Repair each failed citation's own sentence, independently and at once.

    This is the fan-out the pipeline was missing. Fixing citation [7] does not
    depend on fixing [12], so the repairs are genuinely independent work -- but
    they were being done by handing one model the whole report and asking for a
    corrected copy. Measured: a base verify iteration costs ~83 s, of which the
    8-way verification fan-out is ~8 s and the full-report rewrite is the rest,
    three times per run.

    The rewrite is also destructive in ways the surrounding code then repairs:
    it re-authors the References section (so every quote is re-collected and the
    section re-rendered) and drops the appended data tables (which
    _reattach_blocks puts back). Changing one sentence changes one sentence, so
    none of that follows.

    Returns (report, repaired_count). A repair that cannot be placed exactly is
    skipped, not forced -- the programmatic net still redacts whatever fails,
    so the worst case here is the behaviour we already had.
    """
    locatable = [fc for fc in failed
                 if fc.get("claim_sentence")
                 and report.count(fc["claim_sentence"]) == 1]
    if len(locatable) < len(failed):
        # A sentence the verifier quoted but that no longer appears verbatim
        # (or appears twice) cannot be substituted safely.
        stats["repair_unlocatable"] = len(failed) - len(locatable)
    if not locatable:
        return report, 0

    sem = asyncio.Semaphore(SENTENCE_REPAIR_WORKERS)

    async def _one(fc):
        async with sem:
            try:
                result = await bounded(
                    Runner.run(agent,
                               prompts_mod.build_sentence_repair_prompt(fc),
                               context=ctx, max_turns=2),
                    timeout, label="repair [%s]" % fc.get("ref_index"))
            except (Exception, asyncio.TimeoutError) as e:
                logger.warning("[%s][sdk] sentence repair for [%s] failed: %s",
                               job_id, fc.get("ref_index"), e)
                return fc, None
            # Kept RAW. A single sentence has no line breaks, and that is the
            # sharpest signal that the model answered with something else --
            # a preamble, an apology, the report echoed back. Collapsing
            # whitespace first would destroy the evidence.
            return fc, str(result.final_output).strip()

    repaired = rejected = 0
    for fc, text in await asyncio.gather(*[_one(fc) for fc in locatable]):
        original = fc["claim_sentence"]
        # A repair is accepted only if it still looks like ONE sentence taking
        # the place of one sentence. An answer that runs away -- a preamble, an
        # apology, the whole report echoed back -- would silently replace a
        # paragraph, which is the failure mode that makes an unattended
        # substitution dangerous.
        if not text or "\n" in text or len(text) > 2 * len(original) + 120:
            rejected += 1
            continue
        # A DRIFT repair must keep its citation. The quote is real -- that is
        # what "drift" means -- so a sentence narrowed to what the quote does
        # support is still a cited claim, and dropping the marker converts a
        # fixable citation into a lost one. Round 36's first agent replicate
        # repaired 8 sentences and shipped 11 citations against a 15.5 mean,
        # losing 29 sentences to redaction.
        #
        # A TEXT repair is the opposite case: the quote is not in the paper, so
        # the marker SHOULD be able to go, and requiring it would force the
        # model to keep a citation it has just been told is unsupportable.
        marker = "[%d]" % fc.get("ref_index", 0)
        if fc.get("mode") != "text" and marker not in text:
            rejected += 1
            continue
        text = " ".join(text.split())
        if report.count(original) != 1:      # an earlier repair moved it
            rejected += 1
            continue
        report = report.replace(original, text, 1)
        repaired += 1
    stats["sentences_repaired"] = repaired
    if rejected:
        stats["repairs_rejected"] = rejected
    logger.info("[%s][sdk] sentence repair: %d fixed, %d rejected, %d unplaceable",
                job_id, repaired, rejected, len(failed) - len(locatable))
    return report, repaired














class _Heartbeat:
    """Background thread that touches the job's updatedAt every interval.

    The servlet's stale-job check declares a run dead when its record has not
    changed for AI_STALE_JOB_TIMEOUT (10 min). The workflow only reports
    progress at phase boundaries, and the interpretation phase alone can run
    longer than that -- batches with tool loops serialise on the gateway --
    so without this a healthy run was flipped to "error" mid-interpretation
    (measured: 'interpreting 45' at 120 s, watchdog at 721 s, batches still
    running). Runs from before the semaphore is acquired, so a job waiting for
    a permit is also seen as alive rather than dead.
    """

    def __init__(self, job_id, interval=30):
        self._job_id = job_id
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ai-heartbeat-%s" % job_id)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        from src.common.DAO.AIInterpretDAO import AIInterpretDAO
        while not self._stop.wait(self._interval):
            # Best-effort, but never silent, and the connection is closed in a
            # finally: DBManager builds a new MongoClient per DAO, and this
            # beats every 30 s for the whole life of a job.
            dao = None
            try:
                dao = AIInterpretDAO()
                dao.touch(self._job_id)
            except Exception:
                logger.debug("[%s] heartbeat touch failed", self._job_id,
                             exc_info=True)
            finally:
                if dao is not None:
                    dao.closeConnection()





def run_ai_agent(job_id, experiment_design, RESPONSE):
    """Servlet entry point — drop-in replacement for pipeline.run_ai_agent.

    Same contract the servlet and PySiQ rely on: DAO progress milestones the
    UI polls, pathway-index and papers persistence, cancellation via
    _cancel_flags, the concurrency semaphore, and the same RESPONSE payloads.
    The interpretation itself happens inside run_agent_loop_workflow.
    """
    from src.common.JobInformationManager import JobInformationManager
    from src.common.DAO.AIInterpretDAO import AIInterpretDAO

    dao = None
    acquired = False
    # Where this run starts in the process-wide truncation log, so the count
    # stamped below belongs to this job and not to whatever ran before it.
    truncations_at_start = len(_TRUNCATIONS)
    heartbeat = _Heartbeat(job_id)
    try:
        dao = AIInterpretDAO()
        dao.save_progress(job_id, {"status": "extracting", "percent": 5,
                                   "detail": "Loading analysis results..."})
        dao.save_progress(job_id, {"experimentDesign": experiment_design or ""})
        heartbeat.start()

        job_instance = JobInformationManager().loadJobInstance(job_id)
        if job_instance is None:
            raise UserWarning("Job %s was not found." % job_id)

        _agent_semaphore.acquire()
        acquired = True

        hooks = {
            "progress": lambda s, p, d: dao.save_progress(
                job_id, {"status": s, "percent": p, "detail": d}),
            "cancelled": lambda: bool(_cancel_flags.get(job_id)),
            "pathways": lambda pw: dao.save_pathway_index(job_id, pw),
            "partition": lambda part: dao.save_clusters(job_id, part),
        }
        # The Lead Interpreter loop (agent_loop.py; design in docs/diagrams/
        # paintomics-ai-agent-proposal.drawio) is now the only arm. The fixed
        # six-phase workflow that used to sit behind `AI_FULL_AGENT=0` was
        # removed: 16 functions and 1 491 lines, ~60% of this module.
        #
        # It had been dead in production since the arm was enabled, and it was
        # also the benchmark's control arm -- so base-vs-agent comparisons are
        # no longer possible from this tree. That was weighed and accepted.
        from src.classes.AIInterpret.agent_loop import run_agent_loop_workflow
        # A retry appends to the previous run's journal otherwise: the first
        # live re-run left 27 dead events sitting above 34 live ones in one
        # array, with nothing marking the boundary. The journal describes
        # THIS run.
        dao.save_progress(job_id, {"toolTrace": [], "notebook": []})
        hooks["tool_event"] = lambda e: dao.append_tool_event(job_id, e)
        hooks["notebook"] = lambda nb: dao.save_progress(
            job_id, {"notebook": nb})
        out = run_agent_loop_workflow(job_instance, job_id,
                                      experiment_design, hooks=hooks)
        report = out.get("report") or ""
        papers = out.get("papers") or []
        if papers:
            dao.save_papers(job_id, papers)
        # A finished run with an empty report is a failure wearing success's
        # clothes (two such runs scored as data in evolve round 5); refuse it.
        if not report.strip():
            raise RuntimeError("The agent workflow produced an empty report.")

        known = {p.get("ref_index") for p in papers}
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", report)} & known
        with_full = sum(1 for p in papers if p.get("ref_index") in cited
                        and p.get("full_text_available"))
        detail = (f"Ready — {len(cited)} of {len(papers)} retrieved papers "
                  f"cited ({with_full} with full text)")
        stats = out.get("stats") or {}
        dao.save_progress(job_id, {
            "status": "done", "percent": 100, "detail": detail,
            "report": report,
            # The stored contract: "verification" is verify_report_v2's dict
            # (references_section_found, citations_checked, failed_citations);
            # timings and counters live beside it, not inside it.
            "verification": stats.get("verification") or {},
            "stats": dict({k: v for k, v in stats.items() if k != "verification"},
                          truncated_calls=len(_TRUNCATIONS) - truncations_at_start),
        })
        RESPONSE.setContent({"success": True, "jobID": job_id, "status": "done"})

    except InterruptedError:
        if dao:
            dao.save_progress(job_id, {"status": "cancelled", "percent": 0,
                                       "detail": "Cancelled by user"})
        RESPONSE.setContent({"success": True, "jobID": job_id,
                             "status": "cancelled"})
    except Exception as ex:
        logger.exception("agent workflow failed for job %s", job_id)
        if dao:
            try:
                dao.save_progress(job_id, {"status": "error", "percent": 0,
                                           "detail": str(ex)})
            except Exception:
                pass
        from src.common.ServerErrorManager import handleException
        handleException(RESPONSE, ex, __file__, "run_ai_agent")
    finally:
        if acquired:
            _agent_semaphore.release()
        heartbeat.stop()
        _cancel_flags.pop(job_id, None)
        if dao:
            try:
                dao.closeConnection()
            except Exception:
                pass
