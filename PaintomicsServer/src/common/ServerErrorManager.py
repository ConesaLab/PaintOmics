#***************************************************************
#  This file is part of PaintOmics 3
#
#  PaintOmics 3 is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  PaintOmics 3 is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with PaintOmics 3.  If not, see <http://www.gnu.org/licenses/>.
#  Contributors:
#     Rafael Hernandez de Diego <paintomics4@gmail.com>
#     Ana Conesa Cegarra
#     and others
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#
#**************************************************************

import logging
import re
from os import path

# What job ids, example dataset ids and pathway ids are made of.
_QUOTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def quotable(value):
    """`value` as text when a message may repeat it on any page, else None.

    Some answers are read two ways. The Step 4 walk panel and the experiment design
    note set a message as text, while a page still open from before Util.js 3.6, and
    any answer that reaches showErrorMessage directly, inserts the same kind of message
    as HTML. Escaping a value suits only the second: the first then shows "&lt;". So a
    value off the request is repeated only when it is letters, digits, '.', '_' and
    '-', which reads the same both ways (every real job id and dataset id is). The
    caller words the message without it otherwise.
    """
    text = "" if value is None else str(value)
    return text if _QUOTABLE.fullmatch(text) else None


def handleException(responseInstance, exceptionInstance, filename, function, responseStatus=400, userID = ""):
    """Given an exception object, register a new error message for current job.

    Keyword arguments:
    responseInstance -- the HTTP response
    exceptionInstance -- the handled exception
    location -- location where the exception was thrown
    responseStatus -- the response status (default 400)

    str(exceptionInstance) goes out as it is, and it can quote anything: an OSError
    quotes the file name or omic name in its path, int() the value it could not read.
    So the message is text: ajaxErrorHandler, extJSErrorHandler and step1FailureReason
    encode it where they show it. Emphasis is written [b], [br], [ul], [li], never as
    raw HTML.
    """
    #if isinstance(exceptionInstance, CredentialException):
    #    responseStatus=200
    exc_type=None
    exc_tb=None
    file_name = None

    try:
        import sys, os
        exc_type, exc_obj, exc_tb = sys.exc_info()
        file_name = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    except Exception:
        if exc_type == None:
            exc_type = "Unable to get the exception type"
        if exc_tb == None:
            exc_tb = {"tb_lineno" : "Unable to get the exception line"}
        if file_name == None:
            file_name = "Unable to get the exception file name"

    responseMessage = type(exceptionInstance).__name__ + ": AT " + path.basename(filename) + ": " + function + ". ERROR MESSAGE: " + str(exceptionInstance)
    if userID != "" and userID is not None:
        responseMessage += ". User ID: " + str(userID)
    logging.error(responseMessage)

    responseInstance.setStatus(responseStatus)
    responseInstance.setContent({"success": False, "message" :responseMessage, "extra": {
        "exc_type": str(exc_type), "file_name": str(file_name), "exc_line": str(exc_tb.tb_lineno),
        # Tells the client a refusal of the user's files from a fault of ours
        # (see InputFileError); ajaxErrorHandler words the two differently.
        "input_error": isinstance(exceptionInstance, InputFileError),
        # And a job that is no longer there from either (see JobUnavailableError).
        "job_unavailable": isinstance(exceptionInstance, JobUnavailableError),
        # And a job another request is still changing (see JobBusyError).
        "job_busy": isinstance(exceptionInstance, JobBusyError)}})

class CredentialException(Exception):
    pass

class InputFileError(Exception):
    """The uploaded files cannot be analysed as they are, and the message says why.

    Raised by the input validation of each job. It was a plain Exception, so the
    browser titled it "Oops..Internal error!", said retrying would not help and
    offered to report it: both error reports of 2026-09-23 and 2026-09-26 were
    users reporting their own files (six condition columns beside ten; a DESeq2
    table with an up/down column) as a fault of ours.
    """
    pass

class JobUnavailableError(UserWarning):
    """The job a request names is not in the database any more.

    Raised when a page that had the job open asks for it again (Next step,
    a pathway, a save) and it has been removed in the meantime, on schedule or
    by its owner. The message says so. As a plain UserWarning it reached the
    browser as "Oops..Internal error!" with a Report button, the same dialog as
    a crash, while the link to that job already showed it as a warning titled
    "This job is not available". ajaxErrorHandler now shows this one the same way.

    A UserWarning still, so code and tests that expect one keep working.
    """
    pass

class JobBusyError(Exception):
    """Another request is still changing the job, and this one waited its
    limit for it without starting (JobLocks.acquire). The message says so.

    Nothing failed: the request was not run and changed nothing, and the
    same request goes through once the other one has finished (a slider move
    takes seconds). ajaxErrorHandler shows it as a
    warning, "This job is busy", with no Report button and without the
    internal error's "retrying in the browser will not help", which would say
    the opposite of the message.
    """
    pass
