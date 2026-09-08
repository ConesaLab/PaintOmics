"""HTTP surface for the input-conversion agent.

The route is a stateless proxy. It holds the API key, checks the session,
enforces a quota and a concurrency cap, and returns one action. It keeps no
conversation: the browser owns the loop state and sends it back each turn, which
means a worker restart cannot strand a conversion and there is nothing to clean
up when a user closes the tab.

It does NOT wait for the model inline. paintomics4.ini runs processes=1,
threads=4, so four threads serve every request on the site; the CSIC gateway
can take a minute or more per answer. An inline call would hold a quarter of
the site's capacity per conversion and take the site down at four concurrent
users. The work is enqueued and the browser polls, exactly as
aiInterpretInitiate does.

Each turn is bounded (agent_turn.TURN_BUDGET_SECONDS) and a gateway that does
not answer fails the ticket with the reason, which the browser shows. For a
short cooldown after that, new turns are refused at once rather than each
spending the budget to find the same thing out.
"""

import logging
import math
import os
import threading
import time

from src.classes.InputConvert import agent_turn
from src.common.ServerErrorManager import handleException
from src.common.UserSessionManager import UserSessionManager


logger = logging.getLogger(__name__)


def _converter_enabled():
    """Whether this deployment has opted in.

    Read defensively. `src/conf/serverconf.py` is gitignored and PROTECTED from
    the deploy rsync, so an already-installed server keeps a config that predates
    this setting -- and a bare `from src.conf.serverconf import AI_INPUT_CONVERTER`
    then raises ImportError at the use site rather than reporting the feature as
    off. Measured on paintomics.uv.es straight after the first deploy: the route
    answered with an ImportError instead of "not enabled on this server".

    See the note on adding a serverconf setting: the value goes in the local
    config, in example_serverconf.py for fresh installs, AND behind a fallback
    here for every server already running.

    And in deploy/compose.yaml. Its environment block is an allowlist: a key
    that is set in deploy/.env but not listed there never reaches the
    container, and the template's os.getenv() then reads the default. That is
    how paintomics.org ran with the converter off after every deploy while the
    operator believed it on -- the Docker image was rebuilt from a compose file
    that had never carried AI_INPUT_CONVERTER (2026-09-08, thirteen refused
    turns from one user before the email arrived). deploy/smoke-test.sh now
    checks that every .env key reaches the container.
    """
    try:
        from src.conf.serverconf import AI_INPUT_CONVERTER
        return bool(AI_INPUT_CONVERTER)
    except ImportError:
        return os.getenv("AI_INPUT_CONVERTER", "false").lower() == "true"


def converter_enabled():
    """The gate, for other modules: /ai_provider reports it to the browser.

    Delegates rather than aliases so a test that swaps `_converter_enabled`
    changes what the status route says too -- the two must never disagree,
    because the browser trusts the status to decide whether to start at all.
    """
    return _converter_enabled()


# The refusal a switched-off server gives. convert-drawer.js recognises this
# wording ("not enabled on this server") to show the explanation instead of a
# retry box, and format-panel.js prints the same sentence on the upload strip,
# so the three stay one sentence.
NOT_ENABLED_MESSAGE = "AI file conversion is not enabled on this server."

# At most this many conversions may be in flight across the whole server. Each
# holds a queue slot and a share of the gateway's rate limit, which is shared
# with AI report generation -- an unbounded burst here would degrade reports for
# users who are not converting anything.
MAX_CONCURRENT_CONVERSIONS = 2
_inflight = threading.BoundedSemaphore(MAX_CONCURRENT_CONVERSIONS)

# Per-user daily budget. Every attempt costs a gateway call that the server pays
# for, so a runaway client cannot be allowed to spend without limit.
MAX_TURNS_PER_USER_PER_DAY = 120
_usage = {}
_usage_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Gateway cooldown
# ---------------------------------------------------------------------------
# When a turn has just found the gateway not answering, the next click must not
# spend another TURN_BUDGET_SECONDS finding the same thing out. For this long
# after such a failure new turns are refused at once, quoting the reason the
# failed turn recorded. Short on purpose: a recovered gateway is back in use
# within a couple of minutes, and a refused click costs the user a retry, not
# a wait. The first turn that succeeds clears it early.
GATEWAY_COOLDOWN_SECONDS = 90
_gateway_lock = threading.Lock()
_gateway_down_until = 0.0
_gateway_last_error = ""


def _mark_gateway_down(failure):
    global _gateway_down_until, _gateway_last_error
    with _gateway_lock:
        _gateway_down_until = time.monotonic() + GATEWAY_COOLDOWN_SECONDS
        _gateway_last_error = getattr(failure, "fact", None) or str(failure)


def _mark_gateway_up():
    global _gateway_down_until, _gateway_last_error
    with _gateway_lock:
        _gateway_down_until = 0.0
        _gateway_last_error = ""


def _gateway_cooldown_left():
    """Seconds of cooldown remaining, 0 when turns may go out."""
    with _gateway_lock:
        return max(0.0, _gateway_down_until - time.monotonic())


def _reset_gateway_state():
    """Forget any recorded gateway failure (tests)."""
    _mark_gateway_up()


def _spend_turn(user_id):
    day = int(time.time() // 86400)
    with _usage_lock:
        used_day, used = _usage.get(user_id, (day, 0))
        if used_day != day:
            used_day, used = day, 0
        if used >= MAX_TURNS_PER_USER_PER_DAY:
            _usage[user_id] = (used_day, used)
            return False
        _usage[user_id] = (used_day, used + 1)
        return True


def _reject_if_carrying_data(state):
    """The profile must not contain measurement rows.

    The privacy claim made in the UI -- that values never leave the machine --
    is only true if it is enforced here rather than trusted to the client. A
    payload carrying raw rows is refused outright instead of being forwarded.
    """
    banned = ("rows", "data", "values", "records", "first_rows_full")
    def walk(node, depth=0):
        if depth > 6:
            return None
        if isinstance(node, dict):
            for k, v in node.items():
                if k in banned and isinstance(v, list) and len(v) > 60:
                    return k
                found = walk(v, depth + 1)
                if found:
                    return found
        elif isinstance(node, list):
            for v in node[:40]:
                found = walk(v, depth + 1)
                if found:
                    return found
        return None
    return walk(state)


def inputConvertTurn(REQUEST, RESPONSE, QUEUE_INSTANCE, JOB_ID):
    """POST /input_convert/turn -> {ticket}. The browser then polls for it."""
    # Whether THIS request holds a conversion slot that nobody else will
    # release. It is handed to the queued work on enqueue; until then an error
    # here must give it back, and before acquire an error must not touch the
    # semaphore at all -- releasing a slot another conversion holds lets that
    # conversion's own release overflow the bound and fail its job.
    acquired = False
    try:
        userID = REQUEST.cookies.get("userID")
        sessionToken = REQUEST.cookies.get("sessionToken")
        UserSessionManager().isValidUser(userID, sessionToken)

        if not _converter_enabled():
            raise Exception(NOT_ENABLED_MESSAGE)

        state = REQUEST.get_json(force=True, silent=True) or {}

        offending = _reject_if_carrying_data(state)
        if offending:
            raise Exception(
                "The conversion request carried a %r field that looks like raw data. "
                "Only the file's structure is sent." % offending)

        cooldown = _gateway_cooldown_left()
        if cooldown:
            raise Exception(
                "%s a moment ago; conversions are paused for another %d seconds "
                "while it recovers. Please try again then."
                % (_gateway_last_error, math.ceil(cooldown)))

        if not _spend_turn(userID):
            raise Exception("Daily conversion limit reached for this account.")

        if not _inflight.acquire(blocking=False):
            raise Exception("The server is converting other files right now. "
                            "Please try again in a moment.")
        acquired = True

        def work(payload):
            # Runs on a queue worker, which now owns the slot: released here on
            # every path. A gateway failure propagates so the ticket fails with
            # its message, and starts the cooldown for the next click.
            try:
                action = agent_turn.next_action(payload)
            except agent_turn.GatewayUnavailable as failure:
                _mark_gateway_down(failure)
                raise
            finally:
                _inflight.release()
            _mark_gateway_up()
            return action

        # PySiQ takes (fn, args) -- the callable and a tuple, not a closure.
        QUEUE_INSTANCE.enqueue(fn=work, args=(state,), job_id=JOB_ID,
                               timeout=agent_turn.TURN_BUDGET_SECONDS + 30)
        acquired = False                      # the worker owns it from here
        RESPONSE.setContent({"success": True, "ticket": JOB_ID})
    except Exception as ex:
        if acquired:
            _inflight.release()
        handleException(RESPONSE, ex, __file__, "inputConvertTurn")
    finally:
        return RESPONSE


def inputConvertResult(REQUEST, RESPONSE, QUEUE_INSTANCE, ticket):
    """GET /input_convert/turn/<ticket> -> {state, action | message}."""
    try:
        userID = REQUEST.cookies.get("userID")
        sessionToken = REQUEST.cookies.get("sessionToken")
        UserSessionManager().isValidUser(userID, sessionToken)

        job = QUEUE_INSTANCE.fetch_job(ticket)
        if job is None:
            RESPONSE.setContent({"success": True, "state": "unknown"})
            return RESPONSE

        status = str(getattr(job, "status", ""))
        if "FINISHED" in status.upper():
            action = QUEUE_INSTANCE.get_result(ticket, remove=True)
            RESPONSE.setContent({"success": True, "state": "done", "action": action})
        elif "FAILED" in status.upper():
            # Carry the reason, and consume the entry like a finished one:
            # PySiQ removes nothing by itself, so a failed ticket would
            # otherwise sit in its table until the next restart.
            message = (QUEUE_INSTANCE.get_error_message(ticket)
                       or "The conversion service failed on that step.")
            QUEUE_INSTANCE.get_result(ticket, remove=True)
            RESPONSE.setContent({"success": True, "state": "error", "message": message})
        else:
            RESPONSE.setContent({"success": True, "state": "running"})
    except Exception as ex:
        handleException(RESPONSE, ex, __file__, "inputConvertResult")
    finally:
        return RESPONSE
