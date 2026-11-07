#***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#**************************************************************
import codecs
import os
import re
import tempfile

from PIL.Image import open as image_open
from chardet import detect # get the encoding of a file

from src.common.ServerErrorManager import InputFileError

class Singleton(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

class Model (object):
    def parseBSON(self, bsonData):
        bsonData.pop("_id")
        for (attr, value) in bsonData.items():
            setattr(self, attr, value)

    def toBSON(self):
        return  self.__dict__

    def clone(self):
        import copy
        newobj = copy.deepcopy(self) # deep (recursive) copy
        return newobj


def resolveWithin(baseDir, fileName):
    """Resolve `fileName` inside `baseDir`, or None if it escapes.

    Routes that act on a file named by the request used to concatenate:

        os.remove(DESTINATION_DIR + fileName)
        open("{path}/{file}".format(path=userDir, file=fileName))

    A `..` in the name walks out of the directory, which made those an
    arbitrary file delete and an arbitrary file read respectively. Flask's
    `send_from_directory` refuses that on its own, so the download handler was
    safe on the branch that served an attachment and unsafe on the branch that
    streamed the file itself.

    `realpath` is what does the work: it collapses `..` and *also* resolves
    symlinks, so a link planted inside the directory cannot be used to step
    outside it. Containment is then checked with `commonpath` rather than
    `startswith`, because a plain prefix test accepts a sibling directory whose
    name merely begins the same way (`inputData` vs `inputData_evil`).

    Returns the absolute resolved path when it is genuinely under `baseDir`,
    otherwise None. Existence is not required -- the caller decides that -- but
    the base directory itself is refused, since it is not a file to act on.
    """
    import os

    if not fileName or not str(fileName).strip():
        return None

    fileName = str(fileName)
    # A NUL cannot appear in a real name and breaks C-level path handling.
    if "\x00" in fileName:
        return None

    base = os.path.realpath(baseDir)
    # os.path.join drops the base entirely if fileName is absolute, so an
    # absolute path would otherwise be honoured verbatim.
    candidate = os.path.realpath(os.path.join(base, fileName))

    if candidate == base:
        return None

    try:
        if os.path.commonpath([base, candidate]) != base:
            return None
    except ValueError:
        # Different drives on Windows; not the same tree by definition.
        return None

    return candidate


# "\" is an ordinary character in a Linux file name, but a name that carries one
# was written as a Windows path, so it is refused with "/" rather than kept.
_PATH_SEPARATORS = ("/", "\\")


def unsafeNameReason(name):
    """Why `name` cannot be one file name inside a folder, or None when it can.

    For a name off a request: an uploaded file's name, a My Data file or inbuilt
    GTF a form refers to, a job id that picks a folder. It has to name one entry
    of the folder and nothing else, so a separator, "." or "..", and a NUL are
    refused rather than cleaned up. A browser sends the bare name of the file the
    user picked, so a name holding any of them was made by hand. Spaces, dots
    inside the name, letters of any script and long names are fine; the file
    system's own limit (255 bytes on ext4) is left to the file system.
    """
    text = "" if name is None else str(name)
    if not text.strip():
        return "it is empty"
    separator = _separatorReason(text)
    if separator is not None:
        return separator
    if text in (".", ".."):
        return "it is '%s'" % text
    return None


def _separatorReason(text):
    if "\x00" in text:
        return "it contains a NUL character"
    if any(separator in text for separator in _PATH_SEPARATORS):
        return "it contains '/' or '\\'"
    return None


def requireOmicNameForFiles(omicName):
    """`omicName`, or InputFileError when it cannot be part of a file name.

    A job names the files it writes for an omic after the omic:
    "<temporal dir>/<omic name>_matched.txt" in Job.py, the metagenes R script's
    "--output_prefix", MORE's "MORE_output_<omic name>_<date>.tab". The name is
    the form's own text, so "../../x" wrote "x_matched.txt" outside the job.
    Only what makes a name a path is refused: a separator or a NUL. A name with
    one never worked (the folder it names does not exist), and "." or ".." are
    harmless here, since a suffix always follows.
    """
    text = "" if omicName is None else str(omicName)
    reason = _separatorReason(text)
    if reason is not None:
        raise InputFileError(
            "The omic name '%s' cannot be used: %s. PaintOmics names the files "
            "it writes for an omic after it. Rename the omic and try again."
            % (text.replace("\x00", "\\0"), reason))
    return omicName


def fileInFolder(folder, name, what="file"):
    """The path of `name` inside `folder`, or InputFileError saying why not.

    The one gate a file name off a request goes through before it reaches the
    file system: where an upload is saved (DataManagementServlet.saveFile),
    where a form names a file already in My Data or an inbuilt GTF
    (JobInformationManager.saveFiles, MOREServlet) and where a job id picks a
    folder. Uploads used to be saved as "<folder>/<name>" with the name as the
    request gave it, so "../x" wrote a new file outside the user's folder.

    unsafeNameReason refuses anything that is not a single name. resolveWithin
    then resolves the path with realpath, which follows symlinks: a link inside
    the folder that points out of it is refused too, including one whose target
    does not exist yet, which open() would have created.

    InputFileError, so the page says "Please check your input files" and offers
    no error report. The message quotes the name as text; every dialog that
    shows it encodes it.
    """
    reason = unsafeNameReason(name)
    path = None
    if reason is None:
        path = resolveWithin(folder, str(name))
        if path is None:
            reason = "it points outside its folder"
    if reason is not None:
        shown = ("" if name is None else str(name)).replace("\x00", "\\0")
        raise InputFileError(
            "The %s name '%s' cannot be used: %s. A file name must be one name, "
            "without '/' or '\\'. Rename the file and try again." % (what, shown, reason))
    return path


def chunks(l, n):
    """
        This function divides an array in n parts

        @param {Array} l, the array object
        @param {Int} n, number of parts
        @returns list of n arrays
    """
    return [l[i:i+n] for i in range(0, len(l), n)]


def getImageSize(imagePath):
    image = image_open(imagePath)
    return image.size

def unifyAndSort(seq, criteria=None):
    seq = sorted(seq, key=criteria)
    # order preserving
    if criteria is None:
       # The original recipe defined a local identity function here and
       # never bound it, so a call without `criteria` sorted fine and then
       # failed on `criteria(item)` below. Bind the identity instead.
       criteria = lambda item: item  # noqa: E731
    seen = {}
    result = []
    for item in seq:
       marker = criteria(item)
       # in old Python versions:
       # if seen.has_key(marker)
       # but in new ones:
       if marker in seen: continue
       seen[marker] = 1
       result.append(item)
    return result


class RecipientRejected(Exception):
    """The mail server refused the address itself, as one that will never work.

    This is the only delivery failure the server learns about while sending.
    Most bad addresses are accepted here and bounce later, as a message to the
    sender's own mailbox that this server never reads; on paintomics.org a
    Gmail address that does not exist was "sent successfully" six times in one
    run and bounced six times. A refusal of the address at RCPT TO is
    different: the server has told us now that the address will never work,
    so src.common.UserMail turns mail off for that account instead of trying
    it again every day. _deadAddressRefusal decides which refusals say that.
    """

    def __init__(self, address, code, reply):
        self.address = address
        self.code = code
        self.reply = reply
        super(RecipientRejected, self).__init__(
            "The mail server refused %s: %s %s" % (address, code, reply))


# A refusal turns mail off for good, so only the ones about the address itself
# count. With an enhanced status code (RFC 3463, "550 5.1.1 ..."), these:
# 5.1.1 no such mailbox, 5.1.2 no such domain, 5.1.3 malformed address,
# 5.1.6 mailbox moved and no forwarding address, 5.1.10 the domain takes no
# mail (null MX, RFC 7505). Without one, the basic codes for the same (550 no
# such mailbox, 551 user not local, 553 mailbox name not allowed), and only
# when the text says so: a bare 550 is just as often "relaying denied". A
# refusal this misses is tried again the next day and can be turned off by hand.
#
# Every other refusal is raised as an ordinary failure and tried again on a
# later day, even with a 5xx code. Two in particular:
#   * 552 / 5.2.2, mailbox full: RFC 5321 (4.5.3.1.10) says a client SHOULD
#     treat 552 at this step as temporary; the owner empties the mailbox and
#     the address works again;
#   * 5.7.x, refused by policy (relaying denied, a block list, our own
#     sending limits): about us or the route, not the address. Read as a dead
#     address, one misconfigured relay would turn mail off for every account
#     it was asked to reach.
_DEAD_ADDRESS_STATUS = frozenset(("5.1.1", "5.1.2", "5.1.3", "5.1.6", "5.1.10"))
_DEAD_ADDRESS_CODES = frozenset((550, 551, 553))
_DEAD_ADDRESS_WORDS = re.compile(
    r"no such|user unknown|unknown (user|recipient|address|mailbox)|not exist|n.t exist|"
    r"not found|not local|invalid (recipient|address|mailbox)|name not allowed", re.IGNORECASE)
_ENHANCED_STATUS = re.compile(r"^\s*([245]\.\d{1,3}\.\d{1,3})(?![\d.])")


def _deadAddressRefusal(refused):
    """(address, code, reply) when every refused recipient was refused as an
    address that will never work (see _DEAD_ADDRESS_STATUS), else None.

    ``refused`` is SMTPRecipientsRefused.recipients: {address: (code, reply)},
    where reply is bytes.
    """
    if not refused:
        return None
    first = None
    for address, answer in refused.items():
        try:
            code, reply = answer
            code = int(code)
        except (TypeError, ValueError):
            return None
        if isinstance(reply, bytes):
            reply = reply.decode("utf-8", "replace")
        reply = str(reply)
        status = _ENHANCED_STATUS.match(reply)
        if status is not None:
            dead = 500 <= code < 600 and status.group(1) in _DEAD_ADDRESS_STATUS
        else:
            dead = code in _DEAD_ADDRESS_CODES and _DEAD_ADDRESS_WORDS.search(reply) is not None
        if not dead:
            return None
        if first is None:
            first = (address, code, reply)
    return first


def sendEmail(ROOT_DIRECTORY, toEmail, toName, subject, _message, fromEmail=None, fromName=None, isHTML=False):
    """
    Send email using SMTP via SendGrid.

    Args:
        ROOT_DIRECTORY: Base directory path (kept for compatibility, not used)
        toEmail: Recipient email address
        toName: Recipient name
        subject: Email subject
        _message: Email body content (HTML or plain text)
        fromEmail: Sender email (defaults to config value)
        fromName: Sender display name (defaults to config value)
        isHTML: Whether the message is HTML formatted (default: False)

    Raises:
        RecipientRejected: the server refused the recipient as an address that
            will never work (see _deadAddressRefusal)
        Exception: If SMTP credentials are not configured or email sending fails
    """
    import smtplib
    import logging
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.utils import formataddr
    from src.conf.serverconf import (
        EMAIL_FROM_ADDRESS, EMAIL_FROM_DISPLAY,
        SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS
    )

    # Use default sender if not provided
    if fromEmail is None:
        fromEmail = EMAIL_FROM_ADDRESS
    if fromName is None:
        fromName = EMAIL_FROM_DISPLAY

    # Validate SMTP credentials are configured
    if not SMTP_PASSWORD:
        error_msg = "SMTP_PASSWORD is not configured. Please set the environment variable."
        logging.error(error_msg)
        raise Exception(error_msg)

    # Create message
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = formataddr((fromName, fromEmail))
    msg['To'] = formataddr((toName, toEmail))

    # Attach the message body
    mime_type = 'html' if isHTML else 'plain'
    msg.attach(MIMEText(_message, mime_type, 'utf-8'))

    # Send email via SMTP
    try:
        # Connect to SMTP server
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        server.ehlo()

        # Use TLS if configured
        if SMTP_USE_TLS:
            server.starttls()
            server.ehlo()

        # Authenticate
        server.login(SMTP_USERNAME, SMTP_PASSWORD)

        # Send email
        server.sendmail(fromEmail, toEmail, msg.as_string())

        # Close connection. Once sendmail() has returned the server has the
        # message, and nothing QUIT does can take it back. A QUIT that fails
        # used to fall into the handlers below and report the message as not
        # sent, and a caller that tries again (the expiry warnings do) then
        # mailed it a second time -- and once a day while QUIT kept failing.
        try:
            server.quit()
        except Exception as quitError:
            logging.warning("Email to %s was accepted by the mail server, but closing "
                            "the connection failed: %s", toEmail, quitError)
            try:
                server.close()
            except Exception:
                pass

        logging.info(f"Email sent successfully to {toEmail} (Subject: {subject})")
        return

    except smtplib.SMTPRecipientsRefused as e:
        # Before the generic branch below, which would flatten it into a plain
        # Exception and lose the one failure that says "never try again".
        dead = _deadAddressRefusal(e.recipients)
        if dead is not None:
            logging.error("The mail server refused %s: %s %s" % dead)
            raise RecipientRejected(*dead) from e
        error_msg = f"SMTP error sending email: {str(e)}"
        logging.error(error_msg)
        raise Exception(error_msg)
    except smtplib.SMTPException as e:
        error_msg = f"SMTP error sending email: {str(e)}"
        logging.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Failed to send email via SMTP: {str(e)}"
        logging.error(error_msg)
        raise Exception(error_msg)


def adapt_string(the_string):
    try:
        return str(the_string)
    except:
        try:
            import unicodedata
            return str(''.join(c for c in unicodedata.normalize('NFD', the_string) if unicodedata.category(c) != 'Mn'))
        except Exception:
            try:
                import re
                return str(re.sub('[^A-Za-z0-9]+', '', the_string))
            except:
                return "INVALID STRING"


# Encoding is decided from a bounded prefix. Omics uploads routinely reach
# hundreds of megabytes, and running chardet over the whole buffer costs time
# and memory to answer a question the first chunk already settles.
_ENCODING_SNIFF_BYTES = 256 * 1024


def _decodes_as_utf8(sample, isPartial):
    """Whether `sample` is valid UTF-8.

    When the sample is only a prefix of a larger file, a multi-byte character
    can be cut in half at the boundary. That is not evidence of a different
    encoding, so a failure inside the last three bytes is forgiven.
    """
    try:
        sample.decode('utf-8-sig')
        return True
    except UnicodeDecodeError as exc:
        return isPartial and exc.start >= len(sample) - 3


# Containers and spreadsheets are not text in any encoding: transcoding them
# "succeeds" under any single-byte codec and rewrites the archive as mojibake.
# Sniffed by magic number so the user gets the message they actually need.
_BINARY_MAGIC = (
    (b"PK\x03\x04", "an Excel/zip file (.xlsx/.zip)"),
    (b"\xd0\xcf\x11\xe0", "a legacy Excel file (.xls)"),
    (b"\x1f\x8b", "a gzip-compressed file (.gz)"),
    (b"%PDF", "a PDF document"),
)


def _binary_upload_reason(sample):
    """A human-readable description when `sample` opens a known binary format.

    NUL bytes are deliberately NOT treated as evidence here: UTF-16 text is
    full of them and is a supported (transcoded) upload encoding. The NUL
    check lives in ensure_utf8, after chardet has had the chance to call the
    file UTF-16/32.
    """
    for magic, description in _BINARY_MAGIC:
        if sample.startswith(magic):
            return description
    return None


def _whole_file_is_utf8(filepath):
    """Stream-decode the entire file as UTF-8, in bounded memory."""
    decoder = codecs.getincrementaldecoder('utf-8-sig')()
    try:
        with open(filepath, 'rb') as handle:
            while True:
                chunk = handle.read(1 << 20)
                if not chunk:
                    decoder.decode(b'', final=True)
                    return True
                decoder.decode(chunk)
    except UnicodeDecodeError:
        return False


def ensure_utf8(filepath):
    """Rewrite `filepath` as UTF-8 in place when it is in some other encoding.

    Returns None on success, or a human-readable reason when the encoding could
    not be resolved. Callers turn that reason into a validation message: an
    unreadable upload is bad input, and it used to escape as a bare
    UnicodeDecodeError.

    UTF-8 is tested directly before chardet is consulted. Guessing first is
    unsafe: single-byte codecs such as cp1252 accept *any* byte sequence, so a
    misdetected UTF-8 file would be silently rewritten into mojibake rather
    than left alone.

    The prefix bounds only the chardet GUESS. The UTF-8 verdict must cover the
    whole file, because every downstream reader decodes the whole file: a
    multi-MB upload whose single bad byte sat past the sniff window used to
    pass here and then raise UnicodeDecodeError in the csv loop.
    """
    with open(filepath, 'rb') as handle:
        sample = handle.read(_ENCODING_SNIFF_BYTES + 1)

    # An empty file has no encoding to fix; emptiness is reported elsewhere.
    if not sample:
        return None

    binaryReason = _binary_upload_reason(sample)
    if binaryReason is not None:
        return ("this looks like " + binaryReason +
                ", not a text table; please export it as tab-delimited UTF-8 text")

    isPartial = len(sample) > _ENCODING_SNIFF_BYTES
    if _decodes_as_utf8(sample, isPartial):
        if not isPartial or _whole_file_is_utf8(filepath):
            return None
        # The prefix is clean UTF-8 but a later byte is not: fall through to
        # chardet with the evidence we have, exactly as for a bad prefix.

    detected = detect(sample).get('encoding')
    if not detected:
        if b"\x00" in sample:
            return ("this looks like a binary file, not a text table; "
                    "please export it as tab-delimited UTF-8 text")
        return "the character encoding could not be determined, please save the file as UTF-8"

    # NUL bytes in anything but a UTF-16/32 family file mean binary content:
    # single-byte codecs would "decode" it happily and rewrite the upload as
    # mojibake, moving the crash downstream to `_csv.Error: line contains NUL`.
    if b"\x00" in sample and not detected.lower().replace("-", "").startswith(("utf16", "utf32")):
        return ("this looks like a binary file, not a text table; "
                "please export it as tab-delimited UTF-8 text")

    # The guess came from the prefix, so it can be too narrow for the byte
    # that actually offends (an all-ASCII prefix guesses 'ascii' even when a
    # latin-1 byte sits at row 20,000). cp1252/latin-1 are the real-world
    # superset codecs for that shape, so fall through to them before giving
    # up; latin-1 accepts any byte, keeping the transcode total for text-ish
    # files, while true binaries were already rejected above.
    text = None
    candidates = [detected]
    for fallback in ("cp1252", "latin-1"):
        if fallback not in [c.lower() for c in candidates]:
            candidates.append(fallback)
    with open(filepath, 'rb') as handle:
        raw = handle.read()
    for candidate in candidates:
        try:
            text = raw.decode(candidate)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    del raw
    if text is None:
        return ("the file could not be read as " + str(detected) +
                ", please save it as UTF-8")

    # newline='' keeps the line endings exactly as decoded; the readers below
    # already cope with CRLF, and rewriting them here would be a silent edit of
    # the user's file. Written to a sibling temp file and os.replace()d so a
    # concurrent job referencing the same [MyData] file never observes a
    # half-written table.
    directory = os.path.dirname(filepath) or "."
    fd, tmpPath = tempfile.mkstemp(prefix=".utf8_", dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        os.replace(tmpPath, filepath)
    except BaseException:
        try:
            os.remove(tmpPath)
        except OSError:
            pass
        raise
    return None
