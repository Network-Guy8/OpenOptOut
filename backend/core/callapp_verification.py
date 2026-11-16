"""
CallApp verification sender.

Run inside the API container:
  python -m app.core.callapp_verification
  python -m app.core.callapp_verification --send

Never sends during dry run.
"""

import argparse
import json
import re
from datetime import datetime

from .settings_store import load_settings
from .mailboxes import send_from_mailbox
from ..models.database import (
    SessionLocal,
    RemovalRequest,
    FamilyMember,
    Mailbox,
    EmailLog,
)

REQUEST_IDS = (57, 59, 64)
DESTINATION = "support+unlist+verification@callapp.com"
MARKER_PREFIX = "[CallApp verification sent request "
SUBJECT = "Re: verification for unlist"


def normalize_phone(raw):
    """Require an explicit international country code."""
    value = str(raw or "").strip()

    # Accept formatting characters but not extensions or letters.
    if not re.fullmatch(r"\+?[\d\s().-]+", value):
        return None

    digits = re.sub(r"\D", "", value)

    if value.startswith("+"):
        candidate = "+" + digits
    elif digits.startswith("00"):
        candidate = "+" + digits[2:]
    elif len(digits) == 11 and digits.startswith("1"):
        candidate = "+" + digits
    elif len(digits) == 10:
        # Standard US/NANP number: add country code.
        candidate = "+1" + digits
    else:
        return None

    if not re.fullmatch(r"\+[1-9]\d{7,14}", candidate):
        return None

    return candidate


def already_sent(db, request_id):
    marker = f"{MARKER_PREFIX}{request_id}]"
    return db.query(EmailLog).filter(
        EmailLog.direction == "sent",
        EmailLog.subject == marker,
    ).first() is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send verification messages after confirmation",
    )
    args = parser.parse_args()

    db = SessionLocal()

    try:
        settings = load_settings()
        prepared = []

        for request_id in REQUEST_IDS:
            req = db.query(RemovalRequest).filter_by(
                id=request_id
            ).first()

            if req is None:
                raise RuntimeError(
                    f"Request {request_id} not found"
                )

            member = db.query(FamilyMember).filter_by(
                id=req.member_id
            ).first()

            if member is None:
                raise RuntimeError(
                    f"Member missing for request {request_id}"
                )

            if str(getattr(req.status, "value", req.status)) != "sent":
                raise RuntimeError(
                    f"Request {request_id} is not in sent status"
                )

            if already_sent(db, request_id):
                print(
                    f"Request {request_id}: already verified by "
                    "this sender; skipping"
                )
                continue

            try:
                handoff = json.loads(req.handoff_enc or "{}")
                combo = handoff.get("combo") or {}
            except (ValueError, TypeError):
                raise RuntimeError(
                    f"Invalid handoff for request {request_id}"
                )

            phone = normalize_phone(combo.get("phone"))

            if not phone:
                raise RuntimeError(
                    f"Request {request_id}: phone needs "
                    "an explicit country code"
                )

            # All CallApp requests used the shared opt-out
            # alias on mailbox 1, regardless of whether the
            # individual family member has a mailbox assigned.
            from .mailboxes import resolve_mailbox
            mailbox = resolve_mailbox(db, member)

            if mailbox is None:
                raise RuntimeError(
                    "Shared CallApp mailbox 1 not found"
                )

            prepared.append(
                (req, member, mailbox, phone)
            )

            print(
                f"READY: request {request_id}, "
                f"{member.full_name}, "
                f"mailbox {mailbox.id}, "
                f"phone ending {phone[-4:]}"
            )

        if not prepared:
            print("Nothing to send.")
            return

        if not args.send:
            print(
                f"\nDRY RUN: {len(prepared)} messages ready. "
                "No email sent."
            )
            return

        print(
            f"\nAbout to send {len(prepared)} separate "
            f"CallApp verification messages."
        )

        approval = input(
            "Type SEND CALLAPP to approve: "
        )

        if approval != "SEND CALLAPP":
            print("Cancelled. Nothing sent.")
            return

        for req, member, mailbox, phone in prepared:
            # Check again before sending.
            if already_sent(db, req.id):
                print(f"Request {req.id}: skipped duplicate")
                continue

            body = (
                "Hello CallApp Support,\n\n"
                "I am replying to your unlisting verification "
                "request.\n\n"
                "Please remove the following phone number "
                "from CallApp:\n\n"
                f"{phone}\n\n"
                "Please confirm when the unlisting "
                "has been completed.\n"
            )

            result = send_from_mailbox(
                settings,
                mailbox,
                [DESTINATION],
                SUBJECT,
                body,
            )

            if not result.get("ok"):
                print(
                    f"FAILED request {req.id}: "
                    f"{result.get('error', 'unknown error')}"
                )
                continue

            # Log only the request ID and a privacy-safe
            # description, never the phone number.
            db.add(
                EmailLog(
                    request_id=req.id,
                    direction="sent",
                    subject=(
                        f"{MARKER_PREFIX}{req.id}]"
                    ),
                    body_snippet=(
                        "CallApp phone verification reply sent"
                    ),
                    matched_key=None,
                    received_at=datetime.utcnow(),
                )
            )
            db.commit()

            print(
                f"SENT request {req.id}: "
                f"{member.full_name}"
            )

        print(
            "\nFinished. Removal requests remain "
            "unconfirmed pending CallApp's response."
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()


def eligible_requests(db, mailbox):
    """Return pending CallApp requests using this mailbox."""
    from .mailboxes import resolve_mailbox
    from ..models.database import Broker

    results = []

    requests = (
        db.query(RemovalRequest)
        .join(Broker, RemovalRequest.broker_id == Broker.id)
        .filter(RemovalRequest.status == "sent")
        .filter(Broker.name.ilike("%callapp%"))
        .all()
    )

    for req in requests:
        if already_sent(db, req.id):
            continue

        member = db.query(FamilyMember).filter_by(
            id=req.member_id
        ).first()

        if member is None:
            continue

        resolved = resolve_mailbox(db, member)

        if resolved is None or resolved.id != mailbox.id:
            continue

        try:
            handoff = json.loads(req.handoff_enc or "{}")
            phone = normalize_phone(
                (handoff.get("combo") or {}).get("phone")
            )
        except (ValueError, TypeError, AttributeError):
            phone = None

        if phone:
            results.append((req, member, phone))

    return results


def auto_verify(db, settings, mailbox):
    """
    Automatically verify only when exactly one outstanding
    CallApp request can be associated with the mailbox.
    Returns a status string.
    """
    candidates = eligible_requests(db, mailbox)

    if not candidates:
        return "no_pending_request"

    if len(candidates) != 1:
        return "ambiguous"

    req, member, phone = candidates[0]

    if already_sent(db, req.id):
        return "already_sent"

    body = (
        "Hello CallApp Support,\\n\\n"
        "I am replying to your unlisting verification "
        "request.\\n\\n"
        "Please remove the following phone number "
        "from CallApp:\\n\\n"
        f"{phone}\\n\\n"
        "Please confirm when the unlisting "
        "has been completed.\\n"
    )

    result = send_from_mailbox(
        settings, mailbox, [DESTINATION], SUBJECT, body
    )

    if not result.get("ok"):
        return "send_failed"

    db.add(EmailLog(
        request_id=req.id,
        direction="sent",
        subject=f"{MARKER_PREFIX}{req.id}]",
        body_snippet="CallApp phone verification reply sent",
        matched_key=None,
        received_at=datetime.utcnow(),
    ))
    db.commit()

    return "sent"
