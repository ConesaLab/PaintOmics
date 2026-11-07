"""Turn PaintOmics mail off, or back on, for one account.

Use it when a message to a user bounces. The server cannot see bounces: Gmail's
SMTP accepts an address that does not exist and sends the bounce later to our
own mailbox. After reading one there, turn mail off for that address and no
message goes to it again -- not the job-expiry warning, not a password reset,
nothing that goes through src.common.UserMail.sendToUser.

    cd PaintomicsServer
    python -m src.AdminTools.scripts.user_mail status  someone@example.org
    python -m src.AdminTools.scripts.user_mail disable someone@example.org --reason "bounced: no such user"
    python -m src.AdminTools.scripts.user_mail enable  someone@example.org
    python -m src.AdminTools.scripts.user_mail list

On the docker deployment run the same inside the app container:

    docker exec -w /app/PaintomicsServer paintomics-app-1 \\
        python -m src.AdminTools.scripts.user_mail disable someone@example.org --reason "bounced"

The switch is three fields on the user's userCollection document
(mailDisabled, mailDisabledReason, mailDisabledAt). Nothing else about the
account changes: the user can still sign in and their jobs are kept. Exit
status is 1 when no account has the address, so a typo is not mistaken for
success.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../..")))

from src.common import UserMail  # noqa: E402


def _collection():
    from pymongo import MongoClient
    from src.conf.serverconf import MONGODB_DATABASE, MONGODB_HOST, MONGODB_PORT
    client = MongoClient(MONGODB_HOST, MONGODB_PORT, serverSelectionTimeoutMS=10000)
    return client, client[MONGODB_DATABASE]["userCollection"]


def _describe(user):
    state = "OFF" if UserMail.isMailDisabled(user) else "on"
    line = "%s  userID=%s  mail %s" % (user.get("email"), user.get("userID"), state)
    if UserMail.isMailDisabled(user):
        line += "  since %s  reason: %s" % (user.get(UserMail.MAIL_DISABLED_AT_FIELD) or "?",
                                            user.get(UserMail.MAIL_DISABLED_REASON_FIELD) or "-")
    return line


def run(argv, collection):
    parser = argparse.ArgumentParser(
        prog="user_mail", description="Turn PaintOmics mail off or on for one account.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "enable"):
        sub.add_parser(name).add_argument("email")
    disable = sub.add_parser("disable")
    disable.add_argument("email")
    disable.add_argument("--reason", default="turned off by an administrator")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.command == "list":
        users = list(collection.find({UserMail.MAIL_DISABLED_FIELD: True}))
        for user in users:
            print(_describe(user))
        print("%d account(s) with mail turned off." % len(users))
        return 0

    address = UserMail.normalizeAddress(args.email)
    if args.command == "disable":
        matched = UserMail.disableMail(collection, address, args.reason)
    elif args.command == "enable":
        matched = UserMail.enableMail(collection, address)
    else:
        matched = collection.count_documents({"email": address})

    if not matched:
        print("No account has the address %s." % address)
        return 1
    for user in collection.find({"email": address}):
        print(_describe(user))
    return 0


def main(argv=None):
    client, collection = _collection()
    try:
        return run(sys.argv[1:] if argv is None else argv, collection)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
