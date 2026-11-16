"""
Privacy-request pages found on brokers' websites. See core/privacy_finder.py.

  GET  /api/privacy-pages?broker_id=        what was found (all brokers, or one)
  POST /api/privacy-pages/find/{broker_id}  look at a broker's website now (background)
  POST /api/privacy-pages/{id}/dismiss      "that's not it" (remembered)
  POST /api/privacy-pages/{id}/approve-email  use a found address for email opt-outs
"""

import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..models.database import get_db, User, Broker, PrivacyPage, SessionLocal, BrokerWebsiteCandidate
from ..core.access import require_permission
from ..core import privacy_finder as pf

router = APIRouter(prefix="/api/privacy-pages", tags=["privacy-pages"])
_manage = require_permission("brokers.manage")
_RUNNING: set = set()
_LOCK = threading.Lock()


def _out(p: PrivacyPage, names: dict) -> dict:
    return {"id": p.id, "broker_id": p.broker_id, "broker_name": names.get(p.broker_id, ""),
            "url": p.url, "kind": p.kind, "label": p.label, "status": p.status,
            "fillable": pf.is_automation_eligible(p),
            "found_at": p.found_at.isoformat() if p.found_at else None}


@router.get("")
def list_pages(broker_id: Optional[int] = Query(None), db: Session = Depends(get_db), _=Depends(_manage)):
    q = db.query(PrivacyPage).filter(PrivacyPage.status != "dismissed")
    if broker_id is not None:
        q = q.filter(PrivacyPage.broker_id == broker_id)
    rows = q.order_by(PrivacyPage.found_at.desc()).limit(500).all()
    names = {b.id: b.name for b in db.query(Broker).filter(Broker.id.in_({r.broker_id for r in rows})).all()} if rows else {}
    rows.sort(key=lambda p: (pf.PRIORITY.get(p.kind, 9), -(p.score or 0)))
    checked = None
    if broker_id is not None:
        b = db.query(Broker).filter(Broker.id == broker_id).first()
        checked = b.privacy_checked_at.isoformat() if b and b.privacy_checked_at else None
    return {"pages": [_out(p, names) for p in rows], "checked_at": checked,
            "running": broker_id in _RUNNING if broker_id is not None else bool(_RUNNING)}


def _run(broker_id: int):
    db = SessionLocal()
    try:
        b = db.query(Broker).filter(Broker.id == broker_id).first()
        if b is not None:
            pf.run_for_broker(db, b)
    finally:
        db.close()
        with _LOCK:
            _RUNNING.discard(broker_id)


@router.post("/find/{broker_id}")
def find_now(broker_id: int, db: Session = Depends(get_db), _=Depends(_manage)):
    b = db.query(Broker).filter(Broker.id == broker_id).first()
    if not b:
        raise HTTPException(404, "That site is gone.")
    with _LOCK:
        if broker_id in _RUNNING:
            return {"started": False, "running": True}
        _RUNNING.add(broker_id)
    threading.Thread(target=_run, args=(broker_id,), daemon=True).start()
    return {"started": True, "running": True,
            "message": f"Looking at {b.name}'s website. This takes up to a minute."}


def _page(db, page_id) -> PrivacyPage:
    p = db.query(PrivacyPage).filter(PrivacyPage.id == page_id).first()
    if not p:
        raise HTTPException(404, "That result is gone.")
    return p


@router.post("/{page_id}/dismiss")
def dismiss(page_id: int, db: Session = Depends(get_db), _=Depends(_manage)):
    p = _page(db, page_id)
    p.status = "dismissed"
    db.commit()
    return {"ok": True}


@router.post("/{page_id}/approve-email")
def approve_email(page_id: int, db: Session = Depends(get_db), user: User = Depends(_manage)):
    p = _page(db, page_id)
    if p.kind != "email":
        raise HTTPException(400, "Only email addresses can be approved.")
    addr = pf.approve_email(db, p, user.full_name or user.email)
    return {"ok": True, "message": f"Email opt-outs for this broker will go to {addr}."}


# ── Broker website candidate review ─────────────────────────────────────────

@router.get("/website-candidates")
def list_website_candidates(
    status: str = Query("pending"),
    db: Session = Depends(get_db),
    _=Depends(_manage),
):
    if status not in ("pending", "approved", "dismissed", "all"):
        raise HTTPException(400, "Invalid status")

    q = db.query(BrokerWebsiteCandidate, Broker).join(
        Broker, BrokerWebsiteCandidate.broker_id == Broker.id
    )

    if status != "all":
        q = q.filter(BrokerWebsiteCandidate.status == status)

    rows = q.order_by(
        BrokerWebsiteCandidate.broker_id,
        BrokerWebsiteCandidate.id
    ).limit(1000).all()

    return {
        "candidates": [
            {
                "id": c.id,
                "broker_id": b.id,
                "broker_name": b.name,
                "url": c.url,
                "source": c.source,
                "status": c.status,
                "approved_website": b.website,
            }
            for c, b in rows
        ]
    }


@router.post("/website-candidates/{candidate_id}/approve")
def approve_website_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    _=Depends(_manage),
):
    from ..core.privacy_finder import verified_broker_domain

    candidate = db.query(BrokerWebsiteCandidate).filter(
        BrokerWebsiteCandidate.id == candidate_id
    ).first()

    if not candidate:
        raise HTTPException(404, "Candidate not found")

    if candidate.status == "dismissed":
        raise HTTPException(409, "Dismissed candidate cannot be approved")

    broker = db.query(Broker).filter(
        Broker.id == candidate.broker_id
    ).first()

    if not broker:
        raise HTTPException(404, "Broker not found")

    old_website = broker.website
    broker.website = candidate.url

    if not verified_broker_domain(broker):
        broker.website = old_website
        raise HTTPException(400, "Candidate is not a valid website")

    candidate.status = "approved"

    # Keep only one approved candidate per broker.
    others = db.query(BrokerWebsiteCandidate).filter(
        BrokerWebsiteCandidate.broker_id == broker.id,
        BrokerWebsiteCandidate.id != candidate.id,
        BrokerWebsiteCandidate.status == "approved"
    ).all()

    for other in others:
        other.status = "dismissed"

    # A new website can be checked by the privacy finder.
    broker.privacy_checked_at = None

    db.commit()

    return {
        "ok": True,
        "broker": broker.name,
        "website": broker.website,
        "previous_website": old_website,
    }


@router.post("/website-candidates/{candidate_id}/dismiss")
def dismiss_website_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    _=Depends(_manage),
):
    candidate = db.query(BrokerWebsiteCandidate).filter(
        BrokerWebsiteCandidate.id == candidate_id
    ).first()

    if not candidate:
        raise HTTPException(404, "Candidate not found")

    if candidate.status == "approved":
        raise HTTPException(
            409, "Approved websites must be replaced before dismissal"
        )

    candidate.status = "dismissed"
    db.commit()

    return {"ok": True, "candidate_id": candidate_id}
