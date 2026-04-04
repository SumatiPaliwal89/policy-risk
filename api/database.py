"""
api/database.py
SQLAlchemy ORM — persistent audit trail and workflow state for every policy submission.
"""

import os
import json
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "guidewire.db")
_ENGINE  = create_engine(f"sqlite:///{_DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=_ENGINE)
Base = declarative_base()


# ── ORM Models ────────────────────────────────────────────────────────────────

class PolicySubmission(Base):
    __tablename__ = "policy_submissions"

    id                 = Column(Integer, primary_key=True, index=True)
    policy_type        = Column(String(20))
    state              = Column(String(2))
    coverage_amount    = Column(Integer)
    applicant_age      = Column(Integer)
    prior_claims_count = Column(Integer)
    deductible_amount  = Column(Integer)
    workflow_state     = Column(String(30), default="SUBMITTED")
    submitted_at       = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.datetime.utcnow,
                                onupdate=datetime.datetime.utcnow)
    submitted_by       = Column(String(60), default="system")
    notes              = Column(Text, default="")

    clauses    = relationship("ClauseRecord",  back_populates="submission",
                              cascade="all, delete-orphan")
    audit_log  = relationship("AuditEvent",    back_populates="submission",
                              cascade="all, delete-orphan")


class ClauseRecord(Base):
    __tablename__ = "clause_records"

    id              = Column(Integer, primary_key=True, index=True)
    submission_id   = Column(Integer, ForeignKey("policy_submissions.id"))
    index           = Column(Integer)
    original_text   = Column(Text)
    final_text      = Column(Text)        # rewritten or same as original
    risk_label      = Column(String(10))
    risk_score      = Column(Float)
    confidence      = Column(Float)
    flags           = Column(Text, default="[]")   # JSON list
    was_rewritten   = Column(Boolean, default=False)
    state_flags     = Column(Text, default="[]")   # JSON list — state-specific
    decision        = Column(String(20), default="PENDING")  # PENDING/ACCEPTED/OVERRIDDEN
    reviewer_note   = Column(Text, default="")

    submission = relationship("PolicySubmission", back_populates="clauses")

    def flags_list(self):
        return json.loads(self.flags or "[]")

    def state_flags_list(self):
        return json.loads(self.state_flags or "[]")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id            = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("policy_submissions.id"))
    event_type    = Column(String(40))    # SUBMITTED / AI_ASSESSED / DECISION / STATE_FLAG / etc.
    actor         = Column(String(60), default="system")
    description   = Column(Text)
    payload       = Column(Text, default="{}")   # JSON extra data
    created_at    = Column(DateTime, default=datetime.datetime.utcnow)

    submission = relationship("PolicySubmission", back_populates="audit_log")


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    Base.metadata.create_all(_ENGINE)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
