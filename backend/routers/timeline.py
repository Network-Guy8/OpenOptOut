"""
Removal timelines.
  GET /api/timeline/request/{request_id}  one person × one site
  GET /api/timeline/broker/{broker_id}    every person you can see, for one site
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..models.database import get_db, RemovalRequest, Broker, User, RequestStatus, AutomationLog, EmailLog
from ..core.auth import get_current_user, get_accessible_member_ids
from ..core import timeline as tl

router = APIRouter(prefix="/api/timeline", tags=["timeline"])


@router.get("/request/{request_id}")
def request_timeline(request_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    req = db.query(RemovalRequest).filter(RemovalRequest.id == request_id).first()
    if req is None:
        raise HTTPException(404, "That request is gone. Refresh the page.")
    if req.member_id not in set(get_accessible_member_ids(db, current_user)):
        raise HTTPException(403, "You don't have access to this person.")
    return tl.build(db, req)


@router.get("/broker/{broker_id}")
def broker_timelines(broker_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    broker = db.query(Broker).filter(Broker.id == broker_id).first()
    if broker is None:
        raise HTTPException(404, "That site is gone. Refresh the page.")
    allowed = list(get_accessible_member_ids(db, current_user))
    reqs = (db.query(RemovalRequest)
            .filter(RemovalRequest.broker_id == broker_id, RemovalRequest.member_id.in_(allowed))
            .order_by(RemovalRequest.member_id).all())
    # One timeline card per member, retaining all request histories.
    grouped = {}
    for req in reqs:
        grouped.setdefault(req.member_id, []).append(req)

    timelines = []
    for member_reqs in grouped.values():
        member_reqs.sort(key=lambda r: (r.created_at or datetime.min, r.id))
        active = member_reqs[-1]
        combined = tl.build(db, active)

        all_events = []
        for req in member_reqs:
            all_events.extend(tl.raw_events(db, req))

        all_events.sort(key=lambda e: e["at"])
        combined_cycles = tl.cycles(all_events, combined["response_days"])

        def iso(value):
            return value.isoformat() if isinstance(value, datetime) else value

        combined["cycles"] = [
            {
                **{k: iso(v) for k, v in cycle.items()
                   if k not in ("events", "follow_ups")},
                "follow_ups": [iso(v) for v in cycle["follow_ups"]],
                "events": [
                    {**event, "at": iso(event["at"])}
                    for event in cycle["events"]
                ],
            }
            for cycle in combined_cycles
        ]

        timelines.append(combined)

    return {"broker": broker.name, "timelines": timelines}


@router.post("/request/{request_id}/retry")
def retry_failed_request(request_id: int, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """Requeue a failed, never-submitted request without creating a duplicate."""
    from sqlalchemy import or_

    req = db.query(RemovalRequest).filter(
        RemovalRequest.id == request_id
    ).with_for_update().first()

    if req is None:
        raise HTTPException(404, "Request not found.")

    if req.member_id not in set(get_accessible_member_ids(db, current_user)):
        raise HTTPException(403, "You don't have access to this person.")

    if req.status != RequestStatus.failed:
        raise HTTPException(409, "Only failed requests can be retried.")

    if req.sent_at is not None or req.confirmed_at is not None:
        raise HTTPException(409, "This request may already have been submitted.")

    successful = db.query(AutomationLog).filter(
        AutomationLog.request_id == request_id,
        AutomationLog.action.in_(["form_fill", "email_send"]),
        AutomationLog.status == "success",
    ).first()

    if successful:
        raise HTTPException(409, "A successful submission was already recorded.")

    sent_email = db.query(EmailLog).filter(
        EmailLog.request_id == request_id,
        EmailLog.direction == "sent",
    ).first()

    if sent_email:
        raise HTTPException(409, "An outgoing email was already recorded.")

    if req.waiting_for:
        raise HTTPException(409, "This request has another outstanding dependency.")

    if req.notes and (
        "Paused for human CAPTCHA" in req.notes
        or "Stopped at a CAPTCHA" in req.notes
    ):
        raise HTTPException(409, "Resolve the CAPTCHA before retrying.")

    from ..core.broker_gate import request_allowed
    if req.broker and not request_allowed(req, db):
        raise HTTPException(409, "This broker is currently disabled or unavailable.")

    updated = db.query(RemovalRequest).filter(
        RemovalRequest.id == request_id,
        RemovalRequest.status == RequestStatus.failed,
        RemovalRequest.sent_at.is_(None),
        RemovalRequest.confirmed_at.is_(None),
    ).update(
        {RemovalRequest.status: RequestStatus.pending},
        synchronize_session=False,
    )

    if updated != 1:
        db.rollback()
        raise HTTPException(409, "Request is no longer eligible for retry.")

    db.commit()
    return {"request_id": request_id, "status": "pending"}
