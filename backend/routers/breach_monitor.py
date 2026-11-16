"""Authenticated, per-person consent and breach findings; no removal actions."""
import re
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from ..models.database import get_db, User, Identity, BreachMonitor
from ..core.auth import get_current_user, assert_can_view, assert_can_edit
from ..core.access import require_permission
from ..core.limits import LimitedModel
from ..core import breach_monitor as bm

router = APIRouter(prefix="/api/breach-monitor", tags=["breach-monitor"])


class KeyIn(LimitedModel):
    api_key: str = Field(default="", max_length=32)


@router.put("/key")
def save_key(body: KeyIn, _=Depends(require_permission("settings.system"))):
    from ..core.settings_store import load_settings, save_settings, encrypt_setting
    if body.api_key and not re.fullmatch(r"[0-9a-fA-F]{32}", body.api_key):
        raise HTTPException(400, "Enter a 32-character hexadecimal HIBP API key.")
    cfg = load_settings()
    cfg.setdefault("breach_monitor", {})["api_key_enc"] = encrypt_setting(body.api_key)
    save_settings(cfg)
    return {"ok": True}


@router.get("/{member_id}")
def results(member_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_can_view(db, user, member_id)
    row = db.get(BreachMonitor, member_id)
    data = bm.unpack(row)
    identities = db.query(Identity).filter(Identity.member_id == member_id, Identity.kind == "email").all()
    checked = data.get("checked_emails", {})
    valid_ids = {i.id for i in identities}
    matching_ids = {i.id for i in identities if checked.get(str(i.id)) == (i.value or "").strip().lower()}
    return {"configured": bool(bm.api_key()), "enabled": bool(row and row.enabled),
            "emails": [{"id": i.id, "email": i.value} for i in identities],
            "identity_ids": [i for i in data.get("identity_ids", []) if i in valid_ids],
            "findings": [f for f in data.get("findings", []) if f["identity_id"] in matching_ids],
            "last_success": row.last_success_at.isoformat() if bm.snapshot_current(db, row) else None,
            "running": bool(row and row.lease_token and row.lease_until and row.lease_until > bm.now()),
            "error": row.error_code if row else None}


class ConsentIn(LimitedModel):
    identity_ids: list[int] = Field(default_factory=list, max_length=20)
    authorized: bool = False


@router.put("/{member_id}")
def consent(member_id: int, body: ConsentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_can_edit(db, user, member_id)
    if body.identity_ids and not body.authorized:
        raise HTTPException(400, "Confirm that you are authorized to monitor these addresses.")
    try:
        bm.configure(db, member_id, body.identity_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


@router.post("/{member_id}/run")
def run(member_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_can_edit(db, user, member_id)
    if not bm.api_key():
        raise HTTPException(400, "Configure an HIBP API key first.")
    token = bm.claim(db, member_id)
    if not token:
        raise HTTPException(409, "Enable monitoring first, or wait until the current check and cooldown finish.")
    threading.Thread(target=bm.run_member, args=(member_id, token), daemon=True).start()
    return {"started": True}
