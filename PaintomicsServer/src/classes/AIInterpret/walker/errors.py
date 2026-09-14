"""One failure handler for every SDK tool of the walker and the Writer.

The SDK swallows a raising tool into a generic "an error occurred" and carries
on, so a tool that fails on every call looks exactly like a tool the model
chose not to use. The handler logs which tool raised and tells the model.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def tool_failure(tag, name, hint=""):
    """A ``failure_error_function`` for ``@function_tool``: logs under ``tag``
    and answers the model with the tool's name, the error and ``hint``."""
    def handler(ctx, error):
        logger.warning("[%s] tool %s raised: %s", tag, name, error)
        return "TOOL ERROR in %s: %s.%s" % (name, error, (" " + hint) if hint else "")
    return handler
