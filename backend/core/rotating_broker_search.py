"""Persistent, budgeted broker/member SearXNG rotation."""

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import (
    Column, Integer, DateTime, ForeignKey,
    String, UniqueConstraint
)

from ..models.database import Base, engine, FamilyMember
from . import web_search, listing_finder

log = logging.getLogger(__name__)


class BrokerSearchState(Base):
    __tablename__ = "broker_search_rotation_state"

    id = Column(Integer, primary_key=True)
    member_id = Column(
        Integer, ForeignKey("family_members.id"),
        nullable=False, index=True
    )
    broker_id = Column(
        Integer, ForeignKey("brokers.id"),
        nullable=False, index=True
    )
    domain = Column(String(255), nullable=False)
    last_attempt_at = Column(DateTime)
    last_success_at = Column(DateTime)
    last_error = Column(String(255))

    __table_args__ = (
        UniqueConstraint(
            "member_id", "broker_id", "domain",
            name="uq_broker_search_rotation"
        ),
    )


class BrokerSearchAttempt(Base):
    __tablename__ = "broker_search_rotation_attempts"

    id = Column(Integer, primary_key=True)
    attempted_at = Column(DateTime, nullable=False, index=True)
    member_id = Column(Integer, nullable=False)
    broker_id = Column(Integer, nullable=False)
    status = Column(String(12), nullable=False)


def make_query(member, item):
    profile = listing_finder._profile(member)
    domain = item["domain"]
    kind = item.get("searched_for")

    if kind == "phone" and profile.get("phones"):
        digits = re.sub(r"\D", "", profile["phones"][0])[-10:]
        return f'"{digits}" site:{domain}' if digits else None

    if kind == "email" and profile.get("emails"):
        return f'"{profile["emails"][0]}" site:{domain}'

    names = profile.get("names") or []
    places = profile.get("places") or []

    if not names:
        return None

    name = names[0]
    city = places[0].split(",")[0].strip() if places else ""

    if city:
        return f'"{name}" "{city}" site:{domain}'

    return f'"{name}" site:{domain}'


def run(db, cfg):
    sc = (cfg or {}).get("scheduler") or {}

    if not sc.get("broker_search_enabled", False):
        return {"status": "disabled"}

    ws = web_search.settings_of(cfg)

    if not web_search.configured(ws):
        return {"status": "searxng_not_configured"}

    Base.metadata.create_all(
        bind=engine,
        tables=[
            BrokerSearchState.__table__,
            BrokerSearchAttempt.__table__
        ]
    )

    now = datetime.utcnow()
    day_start = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    daily_limit = max(
        1, min(1000, int(sc.get("broker_search_daily_limit") or 100))
    )
    run_limit = max(
        1, min(500, int(sc.get("broker_search_run_limit") or 50))
    )
    recheck = timedelta(
        days=max(1, min(365, int(
            sc.get("broker_search_recheck_days") or 30
        )))
    )

    used = db.query(BrokerSearchAttempt).filter(
        BrokerSearchAttempt.attempted_at >= day_start
    ).count()

    budget = min(run_limit, max(0, daily_limit - used))

    if budget == 0:
        return {
            "status": "budget_exhausted",
            "used_today": used
        }

    candidates = []

    for member in db.query(FamilyMember).order_by(
        FamilyMember.id
    ).all():

        for item in listing_finder.broker_searches(db, member):
            query = make_query(member, item)

            if not query:
                continue

            state = db.query(BrokerSearchState).filter_by(
                member_id=member.id,
                broker_id=item["broker_id"],
                domain=item["domain"]
            ).first()

            if state and state.last_success_at:
                if now - state.last_success_at < recheck:
                    continue

            if state and state.last_error and state.last_attempt_at:
                if now - state.last_attempt_at < timedelta(hours=12):
                    continue

            last = (
                state.last_attempt_at
                if state and state.last_attempt_at
                else datetime.min
            )

            candidates.append((
                last, member.id, item["broker_id"],
                member, item, query, state
            ))

    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    stats = {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "new": 0,
        "added": 0,
        "remaining_budget": budget
    }

    for _, _, _, member, item, query, state in candidates[:budget]:

        if state is None:
            state = BrokerSearchState(
                member_id=member.id,
                broker_id=item["broker_id"],
                domain=item["domain"]
            )
            db.add(state)

        state.last_attempt_at = datetime.utcnow()

        attempt = BrokerSearchAttempt(
            attempted_at=state.last_attempt_at,
            member_id=member.id,
            broker_id=item["broker_id"],
            status="started"
        )

        db.add(attempt)
        db.commit()

        stats["attempted"] += 1

        try:
            def restricted_search(q):
                results = web_search._searxng(ws, q)
                domain = item["domain"].lower()

                def allowed(result):
                    host = (
                        urlparse(result.get("url") or "").hostname or ""
                    ).lower()
                    return (
                        host == domain
                        or host.endswith("." + domain)
                    )

                return [r for r in results if allowed(r)]

            result = web_search.run_for_member(
                db,
                member,
                cfg,
                search=restricted_search,
                pause=0,
                queries_override=[{
                    "label": "broker:" + item["domain"],
                    "query": query
                }]
            )

            if result.get("errors") or result.get("queries", 0) != 1:
                raise RuntimeError(
                    "; ".join(
                        result.get("errors")
                        or ["Search did not execute"]
                    )
                )

            state.last_success_at = datetime.utcnow()
            state.last_error = None
            attempt.status = "ok"

            stats["succeeded"] += 1
            stats["new"] += result.get("new", 0)
            stats["added"] += result.get("added", 0)

        except Exception as exc:
            db.rollback()

            state = db.query(BrokerSearchState).filter_by(
                member_id=member.id,
                broker_id=item["broker_id"],
                domain=item["domain"]
            ).first()

            attempt = db.query(BrokerSearchAttempt).filter_by(
                id=attempt.id
            ).first()

            if state:
                state.last_error = str(exc)[:255]

            if attempt:
                attempt.status = "error"

            stats["failed"] += 1

            log.warning(
                "Broker SearXNG search failed for broker_id=%s: %s",
                item["broker_id"], exc
            )

        db.commit()

    db.query(BrokerSearchAttempt).filter(
        BrokerSearchAttempt.attempted_at <
        now - timedelta(days=45)
    ).delete()

    db.commit()

    stats["remaining_budget"] -= stats["attempted"]

    return stats
