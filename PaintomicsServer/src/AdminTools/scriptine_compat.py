"""Keep scriptine's command parser working on Python 3.11 and later.

DBManager.py hands its command line to scriptinep3 0.3.1, whose
``inspect_args`` still calls ``inspect.getargspec()``. Python 3.11 removed
that function, and 3.11 is what ``.python-version``, CI and the production
image run. Every ``DBManager.py <command>`` -- the deploy scripts, the
Organisms admin page and a plain shell invocation alike -- therefore died with

    AttributeError: module 'inspect' has no attribute 'getargspec'

before it had parsed a single argument (seen on the Drago VM, 2026-09-08,
installing goat). ``getfullargspec`` is a strict superset; scriptine only
unpacks the first four fields, so handing it those is exact, not approximate.

The shim is a separate module so a test can exercise it on the CI
interpreter without importing DBManager, which reads serverconf at import.
"""
import inspect


def ensure_getargspec():
    """Give ``inspect`` a ``getargspec`` if the interpreter dropped it.

    Returns True when the shim was installed, False when the stdlib already
    provided the function (Python <= 3.10) or an earlier call installed it.
    Safe to call more than once.
    """
    if hasattr(inspect, "getargspec"):
        return False

    def _getargspec(function):
        spec = inspect.getfullargspec(function)
        return spec.args, spec.varargs, spec.varkw, spec.defaults

    inspect.getargspec = _getargspec
    return True
