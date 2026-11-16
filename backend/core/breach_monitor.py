"""Opt-in HIBP email monitoring. Never creates removal requests.

Only email identities explicitly selected by an editor are disclosed. Provider
errors and URLs are never logged: request URLs contain the queried address.
"""
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from sqlalchemy import or_

_HTTP_LOCK = threading.Lock()
_next_call = 0.0
API = "https://haveibeenpwned.com/api/v3/breachedAccount/"


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def api_key():
    from .settings_store import load_settings, decrypt_setting
    return decrypt_setting(load_settings().get("breach_monitor", {}).get("api_key_enc", ""))


class ProviderError(Exception):
    def __init__(self, code, retry_after=3600):
        self.code = code
        self.retry_after = max(60, min(int(retry_after), 86400))
        super().__init__(code)


def lookup(email, key, get=None):
    """Fail closed; 404 alone means no known breach. Never follow redirects."""
    global _next_call
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key or ""):
        raise ProviderError("key_required")
    # The minimum HIBP tier permits 10 requests/minute. All local callers share
    # this limiter; this integration targets the default single API process.
    with _HTTP_LOCK:
        wait = _next_call - time.monotonic()
        if wait > 7:
            raise ProviderError("rate_limited", int(wait) + 1)
        time.sleep(max(0, wait))
        try:
            response = (get or requests.get)(API + quote(email, safe=""),
                params={"truncateResponse": "false", "IncludeUnverified": "false"},
                headers={"hibp-api-key": key, "User-Agent": "OpenOptOut-BreachMonitor/0.1"},
                timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            raise ProviderError("provider_unavailable") from None
        finally:
            _next_call = time.monotonic() + 7
        if response.status_code == 404:
            return []
        if response.status_code == 429:
            try:
                retry = int(response.headers.get("Retry-After", 3600))
            except (ValueError, TypeError):
                retry = 3600
            _next_call = time.monotonic() + max(7, min(retry, 86400))
            raise ProviderError("rate_limited", retry)
        if response.status_code in (401, 403):
            raise ProviderError("key_rejected")
        if response.status_code != 200:
            raise ProviderError("provider_unavailable")
        try:
            rows = response.json()
            if not isinstance(rows, list) or len(rows) > 2000:
                raise ValueError()
            output = []
            for r in rows:
                if not isinstance(r, dict) or not isinstance(r.get("Name"), str) or not r["Name"]:
                    raise ValueError()
                classes = r.get("DataClasses", [])
                if not isinstance(classes, list) or not all(isinstance(x, str) for x in classes):
                    raise ValueError()
                output.append({"name": r["Name"][:200], "title": str(r.get("Title") or r["Name"])[:300],
                               "date": str(r.get("BreachDate") or "")[:10],
                               "data_classes": [x[:100] for x in classes[:50]]})
            return output
        except (ValueError, TypeError, KeyError):
            raise ProviderError("invalid_response") from None


def unpack(row):
    return json.loads(row.payload or "{}") if row else {}


def selected_emails(db, member_id, ids):
    from ..models.database import Identity
    rows = db.query(Identity).filter(Identity.member_id == member_id,
                                    Identity.kind == "email", Identity.id.in_(ids)).all()
    return {i.id: i.value.strip().lower() for i in rows
            if i.value and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", i.value.strip())}


def snapshot_current(db, row):
    if not row or not row.last_success_at:
        return False
    data = unpack(row)
    current = selected_emails(db, row.member_id, data.get("identity_ids", []))
    checked = {str(k): v for k, v in current.items()}
    return bool(checked) and checked == data.get("checked_emails", {})


def configure(db, member_id, ids):
    from ..models.database import BreachMonitor
    ids = list(dict.fromkeys(ids))
    if len(ids) > 20 or set(selected_emails(db, member_id, ids)) != set(ids):
        raise ValueError("Choose email addresses saved for this person (up to 20).")
    row = db.get(BreachMonitor, member_id)
    if row is None:
        row = BreachMonitor(member_id=member_id)
        db.add(row)
    # Changing consent cancels a running scan and clears findings. A late
    # worker cannot repopulate them because the lease token is invalidated.
    row.enabled = bool(ids)
    row.payload = json.dumps({"identity_ids": ids, "findings": [], "checked_ids": []})
    row.lease_token = None
    row.lease_until = None
    row.last_success_at = None
    row.next_attempt_at = None
    row.error_code = None
    db.commit()


def claim(db, member_id):
    from ..models.database import BreachMonitor
    stamp = now()
    token = uuid.uuid4().hex
    count = db.query(BreachMonitor).filter(BreachMonitor.member_id == member_id,
        BreachMonitor.enabled.is_(True),
        or_(BreachMonitor.lease_until.is_(None), BreachMonitor.lease_until < stamp),
        or_(BreachMonitor.next_attempt_at.is_(None), BreachMonitor.next_attempt_at <= stamp)
    ).update({BreachMonitor.lease_token: token, BreachMonitor.lease_until: stamp + timedelta(hours=1),
              BreachMonitor.next_attempt_at: stamp + timedelta(minutes=15)}, synchronize_session=False)
    db.commit()
    return token if count else None


def run_member(member_id, token, session_factory=None, key=None, query=None):
    from ..models.database import SessionLocal, BreachMonitor
    db = (session_factory or SessionLocal)()
    try:
        row = db.get(BreachMonitor, member_id)
        if row is None or not row.enabled or row.lease_token != token:
            return
        data = unpack(row)
        emails = selected_emails(db, member_id, data.get("identity_ids", []))
        old = {(f["identity_id"], f["name"]): f for f in data.get("findings", [])}
        findings, checked = [], []
        stamp = now()
        error = None
        retry = 3600
        try:
            if not emails:
                raise ProviderError("no_emails")
            for identity_id, email in emails.items():
                db.expire_all()
                current = db.get(BreachMonitor, member_id)
                if current is None or not current.enabled or current.lease_token != token:
                    return
                rows = (query or lookup)(email, key if key is not None else api_key())
                for breach in rows:
                    previous = old.get((identity_id, breach["name"]), {})
                    findings.append({**breach, "identity_id": identity_id,
                        "first_seen": previous.get("first_seen", stamp.isoformat()),
                        "reviewed": previous.get("reviewed", False)})
                checked.append(identity_id)
        except ProviderError as exc:
            error, retry = exc.code, exc.retry_after
        except Exception:
            # Do not persist exception text; it may contain an email or key.
            error = "provider_unavailable"
        values = {BreachMonitor.lease_token: None, BreachMonitor.lease_until: None,
                  BreachMonitor.error_code: error,
                  BreachMonitor.next_attempt_at: stamp + timedelta(seconds=retry if error else 900)}
        if error is None:
            values[BreachMonitor.last_success_at] = stamp
            values[BreachMonitor.payload] = json.dumps({**data, "findings": findings, "checked_ids": checked, "checked_emails": emails})
        # Failed scans keep the last complete snapshot, never report 'clean'.
        db.query(BreachMonitor).filter(BreachMonitor.member_id == member_id,
            BreachMonitor.enabled.is_(True), BreachMonitor.lease_token == token).update(values, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def scheduled_check():
    from ..models.database import SessionLocal, BreachMonitor
    if not api_key():
        return
    with SessionLocal() as db:
        ids = [r.member_id for r in db.query(BreachMonitor).filter(BreachMonitor.enabled.is_(True)).all()
               if not snapshot_current(db, r) or r.last_success_at < now() - timedelta(days=7)]
    for member_id in ids:
        with SessionLocal() as db:
            token = claim(db, member_id)
        if token:
            run_member(member_id, token)


def purge(db, cfg, stamp):
    """Honor normal artifact retention and the shorter minors' retention."""
    from ..models.database import BreachMonitor, FamilyMember
    from .age import current_age
    privacy = cfg.get("privacy", {})
    for row in db.query(BreachMonitor).all():
        member = db.get(FamilyMember, row.member_id)
        age = current_age(member) if member else None
        days = max(1, int(privacy.get("artifacts_retention_days", 90)))
        if privacy.get("coppa_strict_shredding", True) and age is not None and age < 18:
            days = min(days, max(1, int(privacy.get("coppa_children_retention_days", 30))))
        data = unpack(row)
        cutoff = (stamp - timedelta(days=days)).isoformat()
        # first_seen is deliberate: periodic refresh must not bypass retention.
        retained = [f for f in data.get("findings", []) if f.get("first_seen", "") >= cutoff]
        if len(retained) != len(data.get("findings", [])):
            row.last_success_at = None
            data["checked_ids"] = []
        data["findings"] = retained
        row.payload = json.dumps(data)
