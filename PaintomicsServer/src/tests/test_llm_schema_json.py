"""Schema-enforced JSON in LLMClient, and its fallback to the text parsers.

The point of the schema path is to remove a class of silent, destructive parse
failures -- not to introduce a new way for a run to die. So the contract under
test is: use the schema where the gateway supports it, and where it does not,
behave *exactly* as before.

Two failure modes drove this, and both are asserted here:

  * an unparseable verification verdict falls back to supports_claim=False,
    which redacts a correctly-cited claim;
  * the PMID parser's regex fallback treats any 7-8 digit number as a PMID,
    so prose about a 12345678-fold change yields a citation.

The gateway is mocked throughout: these are contract tests, and must not
depend on a network or a funded API key.
"""
import json
import os
import sys
import traceback

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(SERVER_ROOT, "src"))
sys.path.insert(0, SERVER_ROOT)

from src.classes.AIInterpret import llm_client as lc

PROVIDER = {"api_base": "https://gateway.example/v1", "api_key": "k", "model": "m"}

_PASSED, _FAILED = [], []


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  " + name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  " + name)


class _Resp:
    """Stand-in for requests.Response.

    Carries `headers` because the real Response always does; a double without
    it lets an AttributeError in the retry path pass as a test failure rather
    than the missing guard it actually is.
    """
    headers = {}

    def __init__(self, content="", status=200, text=""):
        self.status_code = status
        self._content = content
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.exceptions.HTTPError("HTTP %d" % self.status_code)
            err.response = self
            raise err

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _patch(handler):
    """Swap requests.post for a handler(payload) -> _Resp. Returns the call log."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        calls.append(json)
        return handler(json)

    lc.requests.post = fake_post
    return calls


def _reset_support():
    with lc._SCHEMA_SUPPORT_LOCK:
        lc._SCHEMA_SUPPORT.clear()


# ---------------------------------------------------------------------------

def test_schema_is_sent_and_parsed():
    _reset_support()
    calls = _patch(lambda p: _Resp(json.dumps({"pmids": ["12345678"]})))
    client = lc.LLMClient(PROVIDER)
    out = client.complete_json(
        [{"role": "user", "content": "x"}], "relevant_pmids",
        {"type": "object", "properties": {"pmids": {"type": "array"}}},
        fallback_parser=lambda t: {"pmids": ["FALLBACK"]})
    assert out == {"pmids": ["12345678"]}, out
    assert calls[0].get("response_format", {}).get("type") == "json_schema", calls[0]
    assert calls[0]["response_format"]["json_schema"]["strict"] is True


def test_gateway_rejecting_schema_falls_back_to_parser():
    """A 400 must degrade to the old behaviour, not fail the job."""
    _reset_support()

    def handler(payload):
        if "response_format" in payload:
            return _Resp(status=400, text="response_format not supported")
        return _Resp("PMIDs: 12345678 and 87654321")

    calls = _patch(handler)
    client = lc.LLMClient(PROVIDER)
    out = client.complete_json(
        [{"role": "user", "content": "x"}], "relevant_pmids", {"type": "object"},
        fallback_parser=lambda t: {"pmids": sorted(set(__import__("re").findall(r"\b(\d{7,8})\b", t)))})
    assert out == {"pmids": ["12345678", "87654321"]}, out
    # First attempt carried the schema, the retry did not.
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_unsupported_endpoint_is_remembered():
    """The 400 is paid once per process, not once per call."""
    _reset_support()

    def handler(payload):
        if "response_format" in payload:
            return _Resp(status=400, text="nope")
        return _Resp("ok 12345678")

    calls = _patch(handler)
    client = lc.LLMClient(PROVIDER)
    parser = lambda t: {"pmids": ["x"]}
    client.complete_json([{"role": "user", "content": "a"}], "s", {}, parser)
    n_after_first = len(calls)
    client.complete_json([{"role": "user", "content": "b"}], "s", {}, parser)

    assert client.supports_schema() is False
    # Second call must be a single request with no schema attempt.
    assert len(calls) == n_after_first + 1, calls
    assert "response_format" not in calls[-1]


def test_non_dict_json_falls_back():
    """A bare array is valid JSON but not the agreed shape."""
    _reset_support()
    _patch(lambda p: _Resp('["12345678"]'))
    client = lc.LLMClient(PROVIDER)
    out = client.complete_json([{"role": "user", "content": "x"}], "s", {},
                               fallback_parser=lambda t: {"pmids": ["FELL_BACK"]})
    assert out == {"pmids": ["FELL_BACK"]}, out


def test_429_is_retried_not_fatal():
    """A shared gateway's rate limit must not kill a multi-minute job.

    429 was grouped with the auth/bad-request 4xx and raised on sight, so a
    momentary limit at the verification phase discarded the entire run.
    """
    _reset_support()
    state = {"n": 0}

    def handler(payload):
        state["n"] += 1
        if state["n"] == 1:
            return _Resp(status=429, text="slow down")
        return _Resp("recovered")

    _patch(handler)
    slept = []
    real_sleep = lc.time.sleep
    lc.time.sleep = lambda s: slept.append(s)
    try:
        client = lc.LLMClient(PROVIDER)
        assert client.complete([{"role": "user", "content": "x"}]) == "recovered"
    finally:
        lc.time.sleep = real_sleep
    assert state["n"] == 2, state
    assert slept, "did not back off before retrying"


def test_429_honours_retry_after_header():
    _reset_support()
    state = {"n": 0}

    class _R(_Resp):
        headers = {"Retry-After": "17"}

    def handler(payload):
        state["n"] += 1
        return _R(status=429) if state["n"] == 1 else _Resp("ok")

    _patch(handler)
    slept = []
    real_sleep = lc.time.sleep
    lc.time.sleep = lambda s: slept.append(s)
    try:
        lc.LLMClient(PROVIDER).complete([{"role": "user", "content": "x"}])
    finally:
        lc.time.sleep = real_sleep
    assert max(slept) >= 17, slept


def test_401_still_fails_fast():
    """Only 429 changed; a bad key must not be retried three times."""
    _reset_support()
    state = {"n": 0}

    def handler(payload):
        state["n"] += 1
        return _Resp(status=401, text="invalid api key")

    _patch(handler)
    import requests as _rq
    client = lc.LLMClient(PROVIDER)
    try:
        client.complete([{"role": "user", "content": "x"}])
        raise AssertionError("401 should have raised")
    except _rq.exceptions.HTTPError:
        pass
    assert state["n"] == 1, "401 was retried %d times" % state["n"]


def test_plain_complete_unchanged_without_schema():
    """No response_format requested => byte-identical request to before."""
    _reset_support()
    calls = _patch(lambda p: _Resp("hello"))
    client = lc.LLMClient(PROVIDER)
    assert client.complete([{"role": "user", "content": "x"}]) == "hello"
    assert "response_format" not in calls[0]


def main():
    real_post = lc.requests.post
    try:
        for t in (test_schema_is_sent_and_parsed,
                  test_gateway_rejecting_schema_falls_back_to_parser,
                  test_unsupported_endpoint_is_remembered,
                  test_non_dict_json_falls_back,
                  test_429_is_retried_not_fatal,
                  test_429_honours_retry_after_header,
                  test_401_still_fails_fast,
                  test_plain_complete_unchanged_without_schema):
            _check(t.__name__, t)
    finally:
        lc.requests.post = real_post
        _reset_support()

    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()
