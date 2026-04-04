"""
api/workflow.py
Workflow state machine for policy submissions.

States:
    SUBMITTED      → initial state, clauses not yet assessed
    AI_ASSESSED    → model has scored all clauses
    LEGAL_REVIEW   → ≥1 HIGH-severity state-rule flag fired → legal team must review
    UW_REVIEW      → all state issues clear, underwriter reviews AI-generated clean policy
    APPROVED       → underwriter or legal team approved
    REJECTED       → sent back for revision

Transitions are guarded — calling an invalid transition raises ValueError.
"""

from __future__ import annotations
import datetime
import json
from sqlalchemy.orm import Session
from api.database import PolicySubmission, ClauseRecord, AuditEvent


# ── Valid transitions ──────────────────────────────────────────────────────────

_TRANSITIONS: dict[str, list[str]] = {
    "SUBMITTED":    ["AI_ASSESSED"],
    "AI_ASSESSED":  ["LEGAL_REVIEW", "UW_REVIEW"],
    "LEGAL_REVIEW": ["UW_REVIEW", "REJECTED"],
    "UW_REVIEW":    ["APPROVED", "REJECTED"],
    "APPROVED":     [],
    "REJECTED":     ["SUBMITTED"],   # allow resubmission after revision
}


def _guard(submission: PolicySubmission, target: str) -> None:
    allowed = _TRANSITIONS.get(submission.workflow_state, [])
    if target not in allowed:
        raise ValueError(
            f"Invalid transition: {submission.workflow_state} → {target}. "
            f"Allowed next states: {allowed or ['(terminal)']}"
        )


def _audit(db: Session, submission: PolicySubmission,
           event_type: str, actor: str, description: str,
           payload: dict | None = None) -> None:
    event = AuditEvent(
        submission_id=submission.id,
        event_type=event_type,
        actor=actor,
        description=description,
        payload=json.dumps(payload or {}),
        created_at=datetime.datetime.utcnow(),
    )
    db.add(event)


# ── Transition helpers ────────────────────────────────────────────────────────

def mark_ai_assessed(
    db: Session,
    submission: PolicySubmission,
    risk_summary: dict,
    state_flag_count: int,
) -> PolicySubmission:
    """Called after the AI scores every clause. Routes to LEGAL_REVIEW or UW_REVIEW."""
    _guard(submission, "AI_ASSESSED")
    submission.workflow_state = "AI_ASSESSED"
    submission.updated_at     = datetime.datetime.utcnow()
    _audit(db, submission, "AI_ASSESSED", "system",
           f"AI assessment complete. Risk summary: {risk_summary}. "
           f"State flags: {state_flag_count}.",
           {"risk_summary": risk_summary, "state_flag_count": state_flag_count})
    db.flush()
    return submission


def route_after_assessment(
    db: Session,
    submission: PolicySubmission,
    has_legal_flags: bool,
) -> PolicySubmission:
    """Route the submission to LEGAL_REVIEW or directly to UW_REVIEW."""
    target = "LEGAL_REVIEW" if has_legal_flags else "UW_REVIEW"
    _guard(submission, target)
    submission.workflow_state = target
    submission.updated_at     = datetime.datetime.utcnow()
    description = (
        "Routed to Legal Review — state-specific regulatory flags require legal sign-off."
        if has_legal_flags
        else "Routed to Underwriter Review — no regulatory flags, AI clean policy ready."
    )
    _audit(db, submission, "ROUTED", "system", description)
    db.flush()
    return submission


def legal_approve(
    db: Session,
    submission: PolicySubmission,
    actor: str = "legal_team",
    note: str = "",
) -> PolicySubmission:
    """Legal team clears the state issues; move to UW_REVIEW."""
    _guard(submission, "UW_REVIEW")
    submission.workflow_state = "UW_REVIEW"
    submission.updated_at     = datetime.datetime.utcnow()
    if note:
        submission.notes = (submission.notes or "") + f"\n[Legal] {note}"
    _audit(db, submission, "LEGAL_APPROVED", actor,
           f"Legal review passed. {note}", {"note": note})
    db.flush()
    return submission


def finalize(
    db: Session,
    submission: PolicySubmission,
    decision: str,
    actor: str,
    note: str = "",
) -> PolicySubmission:
    """
    Underwriter finalizes — decision must be 'APPROVED' or 'REJECTED'.
    """
    decision = decision.upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("decision must be APPROVED or REJECTED")
    _guard(submission, decision)
    submission.workflow_state = decision
    submission.updated_at     = datetime.datetime.utcnow()
    if note:
        submission.notes = (submission.notes or "") + f"\n[UW] {note}"
    _audit(db, submission, decision, actor,
           f"Underwriter decision: {decision}. {note}",
           {"decision": decision, "note": note})
    db.commit()
    return submission


def clause_decision(
    db: Session,
    clause: ClauseRecord,
    decision: str,
    actor: str,
    note: str = "",
) -> ClauseRecord:
    """
    Record a per-clause decision (ACCEPTED or OVERRIDDEN) from the reviewer.
    OVERRIDDEN means the reviewer edited the AI-rewritten text.
    """
    decision = decision.upper()
    if decision not in ("ACCEPTED", "OVERRIDDEN", "PENDING"):
        raise ValueError("decision must be ACCEPTED, OVERRIDDEN, or PENDING")
    clause.decision      = decision
    clause.reviewer_note = note
    _audit(db, clause.submission, "CLAUSE_DECISION", actor,
           f"Clause {clause.index} marked {decision}. {note}",
           {"clause_id": clause.id, "decision": decision})
    db.flush()
    return clause


# ── Queue helpers ─────────────────────────────────────────────────────────────

def get_legal_queue(db: Session) -> list[PolicySubmission]:
    return (db.query(PolicySubmission)
              .filter(PolicySubmission.workflow_state == "LEGAL_REVIEW")
              .order_by(PolicySubmission.submitted_at)
              .all())


def get_uw_queue(db: Session) -> list[PolicySubmission]:
    return (db.query(PolicySubmission)
              .filter(PolicySubmission.workflow_state == "UW_REVIEW")
              .order_by(PolicySubmission.submitted_at)
              .all())
