import requests
import json
import os
import logging
import random
import threading
import time

from . import model_fallback

logger = logging.getLogger(__name__)

# Timeout as (connect, read) tuple.
# - connect: 15s is generous for DNS + TLS handshake
# - read: 180s per chunk — if the API hasn't sent any data in 3 min, it's hung
DEFAULT_TIMEOUT = (15, 180)

# Read timeout for short, high-fan-out calls -- per-citation verification,
# quote lookup, paper filtering. Measured on the CSIC gateway: the median such
# call returns in ~3.5s while roughly one in sixteen stalls for ~60s, and a
# ThreadPoolExecutor waits for the slowest. At the 180s default a single
# straggler holds a phase for three minutes before the retry below (which
# usually succeeds in seconds) even begins. Cutting the read timeout turns the
# existing retry into straggler hedging without new machinery.
SHORT_CALL_TIMEOUT = (15, int(os.getenv("AI_SHORT_CALL_READ_TIMEOUT", "45")))

# Not every OpenAI-compatible gateway implements response_format. Verified
# working on the CSIC gateway (vLLM 0.26.0, guided decoding) on 2026-08-07;
# a self-hosted server behind the same API can still reject it with a 400.
# Probe once per endpoint, remember the answer, and fall back to free-text
# parsing rather than failing the run.
_SCHEMA_SUPPORT = {}       # api_base -> bool
_SCHEMA_SUPPORT_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Aggregate admission control
# ---------------------------------------------------------------------------
# The 429 handler below retries, and that is not the same thing as staying
# under a limit. Every phase here fans out across a ThreadPoolExecutor, so N
# workers hit the ceiling within the same second, each sleeps the SAME fixed
# 5s, and all N retry in the same instant -- a thundering herd that re-triggers
# the limit it is backing off from. Measured on the CSIC gateway (60 req/min):
# six verification sub-agents exhausted all three attempts and the run finished
# with fewer verified citations than it planned.
#
# Retrying is recovery. This is prevention: one token bucket per endpoint,
# shared across every thread in the process, so the AGGREGATE request rate
# cannot exceed the ceiling in the first place.
#
# Default off. A limiter that silently paces production would be a behaviour
# change nobody asked for, and the right ceiling is a property of the token,
# not of the code -- so it is opt-in per deployment via AI_LLM_MAX_RPM.
_LIMITERS = {}             # api_base -> _RateLimiter
_LIMITERS_LOCK = threading.Lock()


class _RateLimiter:
    """Token bucket. `acquire()` blocks until a request may be sent.

    Capacity is one minute's worth of tokens, so a burst after an idle period
    is allowed to run at full width -- the ceiling being respected is a RATE,
    and refusing to burst would cost throughput without protecting anything.
    """

    def __init__(self, rpm):
        self.rate = float(rpm) / 60.0          # tokens per second
        self.capacity = float(rpm)
        self._tokens = float(rpm)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self):
        # Held across the sleep deliberately. Releasing it would let every
        # waiting thread compute the same wait from the same empty bucket and
        # wake together, which is the herd this exists to prevent; serialising
        # the waiters makes them leave one at a time, at the bucket's rate.
        with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity,
                                   self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                time.sleep((1.0 - self._tokens) / self.rate)


def _limiter_for(api_base):
    """The shared limiter for an endpoint, or None when pacing is off."""
    try:
        rpm = int(os.getenv("AI_LLM_MAX_RPM", "0"))
    except (TypeError, ValueError):
        return None
    if rpm <= 0:
        return None
    with _LIMITERS_LOCK:
        lim = _LIMITERS.get(api_base)
        # Rebuild when the setting changes, so a harness can retune between
        # runs in one process without restarting it.
        if lim is None or lim.capacity != float(rpm):
            lim = _RateLimiter(rpm)
            _LIMITERS[api_base] = lim
        return lim


def json_schema_format(name, schema):
    """Build the response_format payload for a strict JSON schema."""
    return {"type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema}}


class MissingAPIKeyError(RuntimeError):
    """This server was never given a token for its configured LLM provider.

    Separate from the HTTPError an authenticated-but-rejected key produces:
    that one may be revoked, expired or rate limited and is worth reporting as
    a gateway problem, while this one is a setting nobody filled in and no
    amount of retrying will change it.
    """


def env_var_for_provider(provider_name):
    """The environment variable serverconf reads that provider's key from.

    serverconf.py names them uniformly -- AI_CSIC_API_KEY, AI_OPENROUTER_API_KEY
    -- so the name can be derived rather than kept in a second list that would
    drift. Hyphens become underscores so a locally-added provider still yields a
    legal variable name.
    """
    return "AI_%s_API_KEY" % str(provider_name).replace("-", "_").upper()


class LLMClient:
    """OpenAI-compatible chat completion via requests. Thread-safe (no global state)."""

    def __init__(self, provider_config, provider_name="csic"):
        self.api_base = provider_config["api_base"].rstrip("/")
        self.model = provider_config["model"]

        # An unset secret is the common case on a fresh checkout, and sending it
        # anyway produces "Bearer " -- which the CSIC gateway rejects with
        # "Malformed API Key ... Ensure Key has `Bearer ` prefix", wording that
        # sends the reader after a prefix the code already sends. Refuse here,
        # before any caller spends a literature-retrieval phase on a run that
        # cannot finish. Stripped because a token pasted out of a web console
        # routinely carries a trailing newline, which the header would forward
        # verbatim and the gateway would reject as a different malformed key.
        self.api_key = (provider_config.get("api_key") or "").strip()
        # The ladder: the configured model, then its fallbacks (see
        # model_fallback). `model_used` names whichever rung answered last,
        # for a caller that records provenance.
        self.provider_name = provider_name
        self.candidates = model_fallback.candidates(provider_config, provider_name)
        self.model_used = None
        if not self.api_key:
            raise MissingAPIKeyError(
                "No API key configured for the '%s' AI provider. Set %s in the "
                "server environment (tokens are self-service at "
                "https://console.llm.iiia.es for the CSIC gateway), then "
                "restart the server. To turn the feature off instead, set "
                "AI_INTERPRETATION_ENABLED=false."
                % (provider_name, env_var_for_provider(provider_name)))

    def supports_schema(self):
        """Whether this endpoint has accepted response_format so far.

        Optimistic until proven otherwise: unknown endpoints are tried once,
        and a 400 demotes them permanently for the process lifetime.
        """
        with _SCHEMA_SUPPORT_LOCK:
            return _SCHEMA_SUPPORT.get(self.api_base, True)

    def _demote_schema(self):
        with _SCHEMA_SUPPORT_LOCK:
            if _SCHEMA_SUPPORT.get(self.api_base, True):
                logger.warning(
                    "Endpoint %s rejected response_format; falling back to "
                    "free-text parsing for the rest of this process.",
                    self.api_base)
            _SCHEMA_SUPPORT[self.api_base] = False

    def complete(self, messages, max_tokens=4096, temperature=0.3,
                 response_format=None, timeout=None, stream=False,
                 max_attempts=3, budget_seconds=None):
        """One chat completion, returned as a string.

        `stream=True` asks the gateway for server-sent events and folds the
        deltas back into one string. On the CSIC gateway that is not an
        optimisation but the difference between an answer and none: LiteLLM
        gives a NON-streamed request about 120 s before it retries the backend
        itself, so a long generation never completes that way, while a stream
        is judged per read and runs for as long as tokens keep coming.

        `budget_seconds` is a wall clock over the WHOLE call -- every attempt,
        every backoff, every model on the ladder and the streamed body itself.
        A caller with someone waiting on the other end (a browser polling a
        ticket) sets it so this call gives up FIRST and says why; without it a
        stream that trickles a token a minute holds a worker and a queue slot
        until the gateway decides to stop. `max_attempts` bounds the retries
        per model the same way. Both default to the behaviour every existing
        caller had.

        The configured model is asked first unless it is known to be down;
        when it fails in a way that says "not served" (see
        model_fallback.falls_back) the next model on the ladder is asked at
        once, and the failed one is skipped for a while. `self.model_used`
        says which one answered.
        """
        # Drop the schema up front on an endpoint already known to reject it,
        # so we pay the 400 once per process rather than once per call.
        if response_format is not None and not self.supports_schema():
            response_format = None

        attempts = max(1, int(max_attempts))
        deadline = (time.monotonic() + budget_seconds) if budget_seconds else None

        ladder = model_fallback.ordered(self.api_base, self.candidates)
        for index, model in enumerate(ladder):
            remaining = ladder[index + 1:]
            try:
                content = self._complete_one(
                    model, messages, max_tokens, temperature, response_format,
                    timeout, stream, attempts, deadline, fail_fast=bool(remaining))
            except Exception as e:
                if model_fallback.falls_back(e):
                    # Remembered even for the last rung: the next call's
                    # ordering, and the probe's verdict, read this.
                    model_fallback.mark_down(self.api_base, model, e)
                    if remaining:
                        logger.warning("LLM model %s failed (%s); asking %s instead",
                                       model, type(e).__name__, remaining[0])
                        continue
                raise
            model_fallback.mark_up(self.api_base, model)
            self.model_used = model
            if model != self.model:
                logger.warning("LLM answer came from fallback model %s, not the "
                               "configured %s", model, self.model)
            return content
        raise RuntimeError("no model configured for provider %r" % self.provider_name)

    def _complete_one(self, model, messages, max_tokens, temperature,
                      response_format, timeout, stream, attempts, deadline,
                      fail_fast=False):
        """The retry loop for ONE model. `fail_fast`: another rung is waiting,
        so a failure that would move to it is raised at once instead of being
        retried here with a backoff."""

        def _backoff(seconds, err):
            # A retry that cannot finish inside the budget only delays an
            # answer nobody will be there to read. Stop here instead.
            if deadline is not None and time.monotonic() + seconds >= deadline:
                raise err
            time.sleep(seconds)

        # One escalation per model: on 2026-09-08 the CSIC gateway answered
        # a plain request with a 500 and the same request streamed with the
        # answer (LiteLLM routes the two to different backends). Cheaper to
        # try than the next model, and the folded stream is the same string.
        retried_streamed = False

        for attempt in range(attempts):
            attempt_timeout = timeout or DEFAULT_TIMEOUT
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise requests.exceptions.Timeout(
                        "LLM call exceeded its budget before attempt %d of %s"
                        % (attempt + 1, model))
                # A socket read that is blocked cannot see the deadline, so
                # the last attempt must not be allowed a read longer than
                # what is left -- or the budget is the budget plus one read
                # timeout, and that attempt outlives the browser's patience.
                connect, read = attempt_timeout
                attempt_timeout = (min(connect, remaining), min(read, remaining))
            try:
                logger.info(f"LLM complete: model={model}, "
                            f"msgs={len(messages)}, max_tokens={max_tokens} "
                            f"(attempt {attempt + 1})")
                payload = {"model": model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}
                if response_format is not None:
                    payload["response_format"] = response_format
                if stream:
                    payload["stream"] = True
                limiter = _limiter_for(self.api_base)
                if limiter is not None:
                    limiter.acquire()
                r = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                    timeout=attempt_timeout,
                    stream=stream,
                )
                r.raise_for_status()
                if stream:
                    content = self._fold_stream(r, deadline)
                else:
                    content = r.json()["choices"][0]["message"]["content"]
                logger.info(f"LLM complete: got {len(content)} chars")
                return content
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"LLM request failed (attempt {attempt + 1}/{attempts}): {e}")
                if fail_fast:
                    raise
                if attempt < attempts - 1:
                    _backoff(5 * (attempt + 1), e)  # 5s, 10s backoff
                    continue
                raise
            except requests.exceptions.HTTPError as e:
                # A 400 while we were asking for a schema is the signature of a
                # gateway that doesn't implement response_format. Retry once
                # without it so the caller's text parser can still run, instead
                # of failing a job over an optional feature.
                if (e.response is not None and e.response.status_code == 400
                        and response_format is not None):
                    self._demote_schema()
                    response_format = None
                    continue
                # 429 is the one 4xx that DOES self-heal. It was being lumped in
                # with auth/bad-request and raised immediately, so a moment of
                # rate limiting on a shared gateway killed a whole multi-minute
                # job at the last phase. Back off and retry, honouring
                # Retry-After when the server sends one. Never a reason to
                # change model: the key is throttled, not the model.
                if e.response is not None and e.response.status_code == 429:
                    if attempt < attempts - 1:
                        wait = 5 * (attempt + 1)
                        try:
                            wait = max(wait, int(e.response.headers.get("Retry-After", 0)))
                        except (AttributeError, TypeError, ValueError):
                            pass  # missing or unusable Retry-After: keep the backoff
                        # Jitter, because a fixed schedule makes every worker
                        # that hit the ceiling in the same second retry in the
                        # same second, re-triggering it. Additive and one-sided
                        # so the wait can only grow -- never dipping back under
                        # a Retry-After the server explicitly asked for.
                        wait += random.uniform(0, 0.5 * wait)
                        logger.warning("LLM rate limited (429), retrying in %ss "
                                       "(attempt %d/%d)", wait, attempt + 1, attempts)
                        _backoff(wait, e)
                        continue
                    logger.error("LLM rate limited (429) after %d attempts", attempts)
                    raise
                # Don't retry other 4xx errors (auth, bad request) — they won't self-heal
                if e.response is not None and 400 <= e.response.status_code < 500:
                    logger.error(f"LLM HTTP {e.response.status_code}: {e.response.text[:500]}")
                    raise
                logger.warning(f"LLM server error (attempt {attempt + 1}/{attempts}): {e}")
                if not stream and not retried_streamed and attempt < attempts - 1:
                    retried_streamed = True
                    stream = True
                    logger.warning("LLM retrying the same request to %s streamed", model)
                    continue
                if fail_fast:
                    raise
                if attempt < attempts - 1:
                    _backoff(5 * (attempt + 1), e)
                    continue
                raise
        # Every branch above returns, raises or continues; this is reached
        # only when the last attempt ended in a `continue` (a schema
        # demotion), which used to return None into a JSON parser.
        raise requests.exceptions.HTTPError(
            "LLM request to %s gave no answer in %d attempts" % (model, attempts))

    @staticmethod
    def _fold_stream(response, deadline):
        """Concatenate the content deltas of a streamed chat completion.

        `deadline` is a time.monotonic() value or None. It is checked between
        events because the per-read timeout cannot see a gateway under load
        that keeps the stream alive at a token a minute. The response is
        closed on every path: a finished stream returns its connection to the
        pool, a cut one is dropped rather than left half-read.
        """
        parts = []
        try:
            for line in response.iter_lines():
                if deadline is not None and time.monotonic() > deadline:
                    raise requests.exceptions.Timeout(
                        "streamed completion exceeded its budget after %d chars"
                        % sum(len(p) for p in parts))
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", "replace")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    # LiteLLM reports a backend failure mid-stream as an event,
                    # not a status code. It is as transient as a 503 would be.
                    raise requests.exceptions.ConnectionError(
                        "gateway error mid-stream: %s" % json.dumps(chunk["error"])[:300])
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    parts.append(delta)
        finally:
            try:
                response.close()
            except Exception:
                pass
        return "".join(parts)

    def complete_json(self, messages, schema_name, schema, fallback_parser,
                      max_tokens=4096, temperature=0.3, timeout=None):
        """Schema-enforced JSON with the hand-rolled parser as a safety net.

        Returns the parsed dict. The schema does the work wherever the gateway
        supports it; ``fallback_parser`` covers gateways that don't and the
        residual case of a model emitting valid-but-unexpected JSON.

        This is deliberately additive: every caller keeps its parser, so the
        worst case is exactly today's behaviour.
        """
        text = self.complete(
            messages, max_tokens=max_tokens, temperature=temperature,
            response_format=json_schema_format(schema_name, schema), timeout=timeout)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return fallback_parser(text)

    def complete_with_tools_json(self, messages, tools, tool_executor,
                                 schema_name, schema, fallback_parser,
                                 max_tokens=4096, temperature=0.3,
                                 max_iterations=5, timeout=None):
        """Tool loop that ends in schema-enforced JSON.

        Why this is two steps rather than one flag on the loop: passing
        ``response_format`` alongside ``tools`` is accepted by the gateway but
        silently defeats it. Grammar-constrained decoding forces the very first
        token to open the JSON object, so the model can never emit a tool call
        -- it answers immediately from priors and returns a confident,
        unevidenced verdict. Verified against the CSIC gateway on 2026-08-07.

        So: run the tool loop unconstrained, let the agent gather its evidence,
        then spend one extra cheap call to coerce the finished answer into the
        schema.
        """
        text = self.complete_with_tools(
            messages, tools, tool_executor, max_tokens=max_tokens,
            temperature=temperature, max_iterations=max_iterations, timeout=timeout)

        # Spend nothing when the model already answered in clean JSON, which is
        # the common case. The coercion call exists to rescue the malformed
        # tail, not to be paid on every citation -- billing a second request per
        # verification is how this phase started tripping the gateway's rate
        # limit.
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = "\n".join(l for l in stripped.split("\n")
                                 if not l.strip().startswith("```")).strip()
        try:
            direct = json.loads(stripped)
            if isinstance(direct, dict):
                return direct
        except (json.JSONDecodeError, ValueError):
            pass

        if not self.supports_schema():
            return fallback_parser(text)

        try:
            coerced = self.complete(
                messages=[
                    {"role": "system",
                     "content": "Convert the analysis below into the required "
                                "JSON object. Do not add, drop, or soften any "
                                "finding -- report exactly what it concluded."},
                    {"role": "user", "content": text},
                ],
                max_tokens=max_tokens, temperature=0.0,
                response_format=json_schema_format(schema_name, schema), timeout=timeout)
            parsed = json.loads(coerced)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Schema coercion failed, using text parser: %s", e)
        except requests.exceptions.RequestException as e:
            logger.warning("Schema coercion request failed, using text parser: %s", e)
        return fallback_parser(text)

    def complete_with_tools(self, messages, tools, tool_executor,
                            max_tokens=4096, temperature=0.3, max_iterations=5,
                            timeout=None):
        """Chat completion with function-calling tool loop.

        Args:
            messages: Conversation messages (will be mutated with tool interactions).
            tools: List of tool definitions in OpenAI function-calling format.
            tool_executor: Callable(tool_name: str, args: dict) -> str.
            max_iterations: Safety cap on tool-call round-trips.

        Returns:
            Final text content from the assistant (after all tool calls resolved).
        """
        for iteration in range(max_iterations):
            for attempt in range(3):  # 2 retries on network errors
                # The rung believed up; a failure below demotes it, so the
                # next attempt reads a different head.
                model = model_fallback.ordered(self.api_base, self.candidates)[0]
                try:
                    logger.info(f"LLM tool loop iter={iteration + 1}/{max_iterations}, "
                                f"model={model}, msgs={len(messages)} (attempt {attempt + 1})")
                    r = requests.post(
                        f"{self.api_base}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "tools": tools,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                        },
                        timeout=timeout or DEFAULT_TIMEOUT,
                    )
                    r.raise_for_status()
                    model_fallback.mark_up(self.api_base, model)
                    self.model_used = model
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    logger.warning(f"LLM tool request failed (attempt {attempt + 1}/3): {e}")
                    model_fallback.mark_down(self.api_base, model, e)
                    if attempt < 2:
                        if len(self.candidates) == 1:
                            time.sleep(5 * (attempt + 1))
                        continue
                    raise
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and 400 <= e.response.status_code < 500:
                        logger.error(f"LLM HTTP {e.response.status_code}: {e.response.text[:500]}")
                        raise
                    logger.warning(f"LLM server error (attempt {attempt + 1}/3): {e}")
                    model_fallback.mark_down(self.api_base, model, e)
                    if attempt < 2:
                        if len(self.candidates) == 1:
                            time.sleep(5 * (attempt + 1))
                        continue
                    raise

            resp_msg = r.json()["choices"][0]["message"]
            tool_calls = resp_msg.get("tool_calls")

            if not tool_calls:
                # No more tool calls — return the final text answer
                content = resp_msg.get("content", "")
                logger.info(f"LLM tool loop done at iter={iteration + 1}, got {len(content)} chars")
                return content

            # Append the assistant message (with tool_calls) to conversation
            messages.append(resp_msg)

            # Execute each tool and append results
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    fn_args = {}

                logger.info(f"LLM tool call: {fn_name}({fn_args})")
                result_str = tool_executor(fn_name, fn_args)
                logger.info(f"LLM tool result: {fn_name} -> {len(result_str)} chars")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })

        # Safety: if we exhausted iterations, do one final call without tools
        logger.warning("Tool loop reached max_iterations, making final call without tools")
        return self.complete(messages, max_tokens=max_tokens, temperature=temperature)

