"""Mail addressed to a user account, and the switch that turns it off.

Every message PaintOmics sends to a user -- the welcome mail, the password
reset, the job-expiry warning -- goes through ``sendToUser``. It adds what
``Util.sendEmail`` cannot know, because it needs the account and not only the
address:

1. It sends nothing to an account whose mail is turned off: ``mailDisabled``
   is true on its userCollection document.
2. It sends nothing to a guest account. Their addresses are made up
   (``guest12345@`` the site's ``PAINTOMICS_EMAIL_DOMAIN``), so every message
   to one is a bounce.
3. When the mail server refuses the address while sending, as one that will
   never work (``Util.RecipientRejected``: "550 5.1.1 no such user" and the
   like, see ``Util._deadAddressRefusal``), it turns mail off for that
   account, so the next run does not try again. A full mailbox or a refusal
   by policy does not turn anything off; it fails like any other send.

Point 3 catches little in practice. Gmail's SMTP, which paintomics.org sends
through, accepts an address that does not exist and bounces it later, as a
message to the sender's mailbox that this server cannot see. Those addresses
are turned off by an administrator, by hand, after reading the bounce:

    cd PaintomicsServer
    python -m src.AdminTools.scripts.user_mail disable someone@example.org \\
        --reason "bounced: address does not exist"

On the docker deployment, prefix ``docker exec -w /app/PaintomicsServer
paintomics-app-1``. ``status``, ``list`` and ``enable`` are documented in that
script.

The maintainers' own notifications (error reports, organism requests in
``adminServletSendReport``) are not mail to a user account and do not come
through here.
"""
import datetime
import logging

from src.common.Util import RecipientRejected, sendEmail

# Fields on the userCollection document.
MAIL_DISABLED_FIELD = "mailDisabled"
MAIL_DISABLED_REASON_FIELD = "mailDisabledReason"
MAIL_DISABLED_AT_FIELD = "mailDisabledAt"

# What sendToUser did.
SENT = "sent"
SKIPPED = "skipped"      # mail is off for the account, or it is a guest
REJECTED = "rejected"    # the server refused the address; mail is now off


def _field(user, name):
    """A field of a userCollection document or of a User instance."""
    if isinstance(user, dict):
        return user.get(name)
    return getattr(user, name, None)


def normalizeAddress(email):
    """Addresses are stored lowercased (userManagementSignUp)."""
    return (email or "").strip().lower()


def isMailDisabled(user):
    return bool(_field(user, MAIL_DISABLED_FIELD))


def whyNotMail(user):
    """Why this account must not be mailed, or None if it may."""
    if not normalizeAddress(_field(user, "email")):
        return "the account has no address"
    if _field(user, "is_guest"):
        return "it is a guest account, whose address is made up"
    if isMailDisabled(user):
        return "mail is turned off for it (%s)" % (
            _field(user, MAIL_DISABLED_REASON_FIELD) or "no reason recorded")
    return None


def disableMail(userCollection, email, reason):
    """Turn mail off for the account(s) stored with this address.

    Returns how many accounts matched, so a caller can tell a typo (0) from
    success.
    """
    address = normalizeAddress(email)
    if not address:
        return 0
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    result = userCollection.update_many(
        {"email": address},
        {"$set": {MAIL_DISABLED_FIELD: True,
                  MAIL_DISABLED_REASON_FIELD: str(reason or ""),
                  MAIL_DISABLED_AT_FIELD: stamp}})
    return result.matched_count


def enableMail(userCollection, email):
    """Undo disableMail. Returns how many accounts matched."""
    address = normalizeAddress(email)
    if not address:
        return 0
    result = userCollection.update_many(
        {"email": address},
        {"$unset": {MAIL_DISABLED_FIELD: "",
                    MAIL_DISABLED_REASON_FIELD: "",
                    MAIL_DISABLED_AT_FIELD: ""}})
    return result.matched_count


def sendToUser(user, subject, message, ROOT_DIRECTORY="", userCollection=None, isHTML=True):
    """Send one message to the account ``user`` (a userCollection dict or a User).

    Returns SENT; SKIPPED when the account must not be mailed (see whyNotMail),
    without contacting the mail server; or REJECTED when the server refused the
    address, in which case mail is turned off for the account when
    ``userCollection`` is given. Every other failure raises, exactly as
    sendEmail does, so the caller decides whether it is worth trying again.
    """
    email = normalizeAddress(_field(user, "email"))
    reason = whyNotMail(user)
    if reason is not None:
        logging.info("Not mailing account %s (%s): %s.",
                     _field(user, "userID"), email or "-", reason)
        return SKIPPED
    try:
        sendEmail(ROOT_DIRECTORY, email, _field(user, "userName"), subject, message,
                  isHTML=isHTML)
    except RecipientRejected as ex:
        if userCollection is not None:
            try:
                disableMail(userCollection, email,
                            "refused by the mail server while sending: %s %s"
                            % (ex.code, ex.reply))
                logging.warning("Mail turned off for %s: %s", email, ex)
            except Exception as storeEx:
                logging.error("Could not turn mail off for %s after it was refused: %s",
                              email, storeEx)
        return REJECTED
    return SENT
