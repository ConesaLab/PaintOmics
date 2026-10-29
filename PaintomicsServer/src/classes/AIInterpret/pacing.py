"""One request budget per gateway endpoint, shared by every caller in the process.

Retrying a 429 is recovery; this is prevention. Two transports talk to the same
gateway -- LLMClient (requests, on threads: the card, the checks, the paper
agents, the Narrator, the chat) and the Agents SDK shim in agent.py (asyncio,
one event loop per walk: the walkers and the Writers) -- and the key's limit
counts them together. They used to keep separate budgets (llm_client's token
bucket, agent's _AsyncPacer), so AI_LLM_MAX_RPM=N let the process send up to
2N a minute; and the bucket held a minute of tokens, so after an idle spell it
let N through at once and N more within the same 60 s -- 2N in exactly the
rolling window LiteLLM's key limit is enforced over.

Here both acquire from one Pacer per endpoint, which spaces requests
60/rpm seconds apart: no 60-second window, wherever it starts, holds more than
rpm of them. The reservation is taken under a threading.Lock with no wait
inside it; the caller then sleeps on its own -- a thread with time.sleep, a
coroutine with asyncio.sleep -- so no event loop is ever blocked and no asyncio
primitive is bound to one loop.

The ceiling is a property of the token, not of the code. AI_LLM_MAX_RPM sets it
when a deployment says so (0 turns pacing off). Unset, it is read from the
token: the CSIC LiteLLM gateway answers every request with
``x-litellm-key-rpm-limit`` (60 on the production key, measured 2026-10-28),
and from the first answer that carries it every caller is paced at
LEARNED_SHARE of it. A gateway that sends no such header is not paced, as
before. Unpaced, one hsa05219 walk on 2026-10-28 burst its Writers and paper
agents past 60/min; seven paper agents gave up on "429 Client Error", no
statement got confirmed evidence, and the walk kept none.
"""
import asyncio
import heapq
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

RPM_HEADER = "x-litellm-key-rpm-limit"
# Paced a little under the key's limit: the gateway's minute and ours do not
# start at the same instant, and another process may share the key.
LEARNED_SHARE = 0.9


class Pacer(object):
    """Admits one request every 60/rpm seconds, across threads and loops.

    A slot is taken before its caller sleeps, so a caller that never sends --
    a Writer cancelled at its stage's deadline while it waited (parallel._wait),
    a call whose budget the wait would have spent -- hands its slot back
    (release), and the next reservation takes it. Without that a walk's
    cancelled fan-out left phantom slots the Narrator queued behind. The very
    slot is reused, rather than the schedule pulled one interval earlier: with
    a slot handed back from the middle of the queue, pulling the schedule in
    lands the next reservation on a slot a waiter still holds, and some 60 s
    window then holds one request more than rpm.
    """

    def __init__(self, rpm, clock=time.monotonic):
        self.rpm = int(rpm)
        self.interval = 60.0 / self.rpm
        self._clock = clock
        self._next = None
        self._free = []                # heap of the start times handed back
        self._lock = threading.Lock()

    def _take(self):
        """(start, delay): the earliest slot handed back that is still ahead,
        else the next one on the schedule, taken now."""
        with self._lock:
            now = self._clock()
            while self._free and self._free[0] < now:
                heapq.heappop(self._free)          # its time passed unused
            if self._free:
                start = heapq.heappop(self._free)
            else:
                start = now if self._next is None else max(now, self._next)
                self._next = start + self.interval
            return start, start - now

    def reserve(self):
        """Take the next free slot now; the seconds to wait until it opens."""
        return self._take()[1]

    def release(self, start):
        """Hand back the slot that opens at ``start`` (as _take gave it), unsent."""
        with self._lock:
            if start >= self._clock():
                heapq.heappush(self._free, start)

    def _admit(self, seconds_left, floor):
        """A slot and its delay, or (start, None) -- the slot handed back --
        when waiting for it would leave the caller under ``floor`` of its
        ``seconds_left``. A slot open now is never refused: the caller's own
        floor decides that, as it did before pacing."""
        start, delay = self._take()
        if delay > 0 and seconds_left is not None and seconds_left - delay < floor:
            self.release(start)
            return start, None
        return start, delay

    def acquire(self, seconds_left=None, floor=0.0):
        """Block this thread until its slot opens; True. False at once, with
        the slot handed back, when the wait would leave under ``floor`` of the
        caller's ``seconds_left`` (None: no budget, always wait)."""
        delay = self._admit(seconds_left, floor)[1]
        if delay is None:
            return False
        if delay > 0:
            time.sleep(delay)
        return True

    async def wait(self, seconds_left=None, floor=0.0):
        """acquire without blocking the event loop. A waiter cancelled before
        its slot opens hands the slot back."""
        start, delay = self._admit(seconds_left, floor)
        if delay is None:
            return False
        if delay > 0:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                self.release(start)
                raise
        return True


_PACERS = {}               # api_base -> Pacer
_LEARNED = {}              # api_base -> the key's own limit, from its header
_LOCK = threading.Lock()


def _key(api_base):
    return str(api_base or "").rstrip("/")


def _setting():
    """AI_LLM_MAX_RPM as an int, or None when it is unset or unreadable."""
    raw = os.getenv("AI_LLM_MAX_RPM")
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("AI_LLM_MAX_RPM=%r is no number; pacing from the gateway's header instead", raw)
        return None


def ceiling(api_base):
    """Requests per minute every caller of this endpoint is held to; 0 = unpaced."""
    rpm = _setting()
    if rpm is not None:
        return max(0, rpm)
    learned = _LEARNED.get(_key(api_base))
    return max(1, int(learned * LEARNED_SHARE)) if learned else 0


def pacer_for(api_base):
    """The endpoint's one Pacer, or None when it is unpaced. Rebuilt when the
    ceiling changes (a header learned, a harness retuning between runs)."""
    rpm = ceiling(api_base)
    if not rpm:
        return None
    key = _key(api_base)
    with _LOCK:
        pacer = _PACERS.get(key)
        if pacer is None or pacer.rpm != rpm:
            pacer = _PACERS[key] = Pacer(rpm)
        return pacer


def learn(api_base, headers):
    """Read the key's limit off a gateway answer's headers. Only the first
    reading of a value is logged; AI_LLM_MAX_RPM, when set, wins over it."""
    if not headers:
        return
    try:
        limit = int(float(headers.get(RPM_HEADER)))
    except (AttributeError, TypeError, ValueError):
        return
    if limit <= 0:
        return
    key = _key(api_base)
    with _LOCK:
        if _LEARNED.get(key) == limit:
            return
        _LEARNED[key] = limit
    if _setting() is None:
        logger.info("[AI] the gateway key at %s allows %d requests/min; every caller is paced at %d/min "
                    "from now on", key, limit, ceiling(key))


def describe():
    """The pacing setting, for the transport's start-up log line."""
    rpm = _setting()
    if rpm is None:
        return "learned from the gateway's %s header" % RPM_HEADER
    return "%d rpm (AI_LLM_MAX_RPM)" % rpm if rpm > 0 else "off (AI_LLM_MAX_RPM=0)"


def reset():
    """Forget every pacer and learned limit (tests)."""
    with _LOCK:
        _PACERS.clear()
        _LEARNED.clear()
