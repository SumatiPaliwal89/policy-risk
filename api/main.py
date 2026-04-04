"""
api/main.py
FastAPI risk assessment microservice — Guidewire PolicyCenter integration.

Run:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Core endpoints:
    GET  /health
    POST /assess-risk
    POST /bulk-assess
    POST /generate-policy         (legacy single-shot generation)

Workflow endpoints:
    POST /submit-policy           submit clauses → triggers AI assessment
    GET  /submissions             list all submissions
    GET  /submissions/{id}        full submission detail with clauses
    POST /submissions/{id}/clauses/{cid}/decide   per-clause reviewer decision
    POST /submissions/{id}/legal-approve          legal team sign-off
    POST /submissions/{id}/finalize               UW approve/reject
    GET  /queue/legal             legal review queue
    GET  /queue/uw                underwriter review queue
    GET  /audit-log               full audit trail (all submissions)
    POST /upload-pdf              extract clauses from uploaded PDF
"""

import json
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from api.schemas import (
    BulkRequest, BulkResponse, PolicyRequest, RiskResponse,
    GeneratePolicyRequest, GeneratePolicyResponse,
    SubmissionRequest, SubmissionOut, ClauseOut,
    ClauseDecision, FinalizeRequest,
    AuditEventOut, QueueItem, StateFlagOut,
)
from api.predictor import get_predictor
from api.database import init_db, get_db, PolicySubmission, ClauseRecord, AuditEvent
from api.workflow import (
    mark_ai_assessed, route_after_assessment,
    legal_approve, finalize, clause_decision,
    get_legal_queue, get_uw_queue,
)

app = FastAPI(
    title="Guidewire AI Risk Assessment",
    description=(
        "AI-based risk assessment for new insurance policy clauses "
        "with full workflow, audit trail, state rules, and PDF upload."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    get_predictor()


# ── Utilities ─────────────────────────────────────────────────────────────────

def _meta_dict(meta) -> dict:
    return {
        "policy_type":        meta.policy_type,
        "coverage_amount":    meta.coverage_amount,
        "applicant_age":      meta.applicant_age,
        "prior_claims_count": meta.prior_claims_count,
        "deductible_amount":  meta.deductible_amount,
        "state":              meta.state,
    }


def _submission_out(sub: PolicySubmission) -> SubmissionOut:
    """Convert ORM object → Pydantic SubmissionOut (with risk_summary computed)."""
    risk_summary: dict = {"Low": 0, "Medium": 0, "High": 0}
    state_flag_count   = 0
    generated_lines    = []

    clause_outs = []
    for c in sorted(sub.clauses, key=lambda x: x.index):
        risk_summary[c.risk_label] = risk_summary.get(c.risk_label, 0) + 1
        sf = json.loads(c.state_flags or "[]")
        state_flag_count += len(sf)
        generated_lines.append(f"{c.index + 1}. {c.final_text}")
        clause_outs.append(ClauseOut(
            id=c.id,
            index=c.index,
            original_text=c.original_text,
            final_text=c.final_text,
            risk_label=c.risk_label,
            risk_score=c.risk_score,
            confidence=c.confidence,
            flags=json.loads(c.flags or "[]"),
            state_flags=sf,
            was_rewritten=c.was_rewritten,
            decision=c.decision,
            reviewer_note=c.reviewer_note or "",
        ))

    return SubmissionOut(
        id=sub.id,
        policy_type=sub.policy_type,
        state=sub.state,
        coverage_amount=sub.coverage_amount,
        applicant_age=sub.applicant_age,
        prior_claims_count=sub.prior_claims_count,
        deductible_amount=sub.deductible_amount,
        workflow_state=sub.workflow_state,
        submitted_at=sub.submitted_at,
        updated_at=sub.updated_at,
        submitted_by=sub.submitted_by,
        notes=sub.notes or "",
        risk_summary=risk_summary,
        state_flag_count=state_flag_count,
        clauses=clause_outs,
        generated_policy="\n\n".join(generated_lines),
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "model_version": "3.0"}


# ── Single-clause assessment ──────────────────────────────────────────────────

@app.post("/assess-risk", response_model=RiskResponse, tags=["Assessment"])
def assess_risk(request: PolicyRequest):
    predictor   = get_predictor()
    policy_meta = {
        "policy_type":        request.policy_type,
        "coverage_amount":    request.coverage_amount,
        "applicant_age":      request.applicant_age,
        "prior_claims_count": request.prior_claims_count,
        "deductible_amount":  request.deductible_amount,
        "state":              request.state,
    }
    try:
        result = predictor.predict(request.clause, policy_meta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return RiskResponse(**result)


# ── Bulk assessment ───────────────────────────────────────────────────────────

@app.post("/bulk-assess", response_model=BulkResponse, tags=["Assessment"])
def bulk_assess(request: BulkRequest):
    predictor = get_predictor()
    results   = []
    for item in request.clauses:
        policy_meta = {
            "policy_type":        item.policy_type,
            "coverage_amount":    item.coverage_amount,
            "applicant_age":      item.applicant_age,
            "prior_claims_count": item.prior_claims_count,
            "deductible_amount":  item.deductible_amount,
            "state":              item.state,
        }
        try:
            result = predictor.predict(item.clause, policy_meta)
        except Exception:
            result = {
                "risk_label": "High", "risk_score": 0.0, "confidence": 0.0,
                "lr_probabilities": {}, "flags": ["prediction_error"],
                "state_flags": [], "rewritten_clause": None,
            }
        results.append(RiskResponse(**result))

    flagged = sum(1 for r in results if r.risk_label == "High")
    return BulkResponse(total=len(results), flagged=flagged, results=results)


# ── Legacy generate-policy ────────────────────────────────────────────────────

@app.post("/generate-policy", response_model=GeneratePolicyResponse, tags=["Assessment"])
def generate_policy(request: GeneratePolicyRequest):
    predictor   = get_predictor()
    policy_meta = _meta_dict(request.meta)
    try:
        result = predictor.generate_policy(request.clauses, policy_meta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return GeneratePolicyResponse(**result)


# ── Workflow: submit policy ───────────────────────────────────────────────────

@app.post("/submit-policy", response_model=SubmissionOut, tags=["Workflow"])
def submit_policy(request: SubmissionRequest, db: Session = Depends(get_db)):
    """
    Submit a full policy for AI assessment.
    Creates a PolicySubmission, scores every clause, applies state rules,
    rewrites risky clauses via Gemini, then routes to LEGAL_REVIEW or UW_REVIEW.
    """
    predictor   = get_predictor()
    policy_meta = _meta_dict(request.meta)

    # Create submission record
    sub = PolicySubmission(
        policy_type=request.meta.policy_type,
        state=request.meta.state,
        coverage_amount=request.meta.coverage_amount,
        applicant_age=request.meta.applicant_age,
        prior_claims_count=request.meta.prior_claims_count,
        deductible_amount=request.meta.deductible_amount,
        submitted_by=request.submitted_by,
        workflow_state="SUBMITTED",
    )
    db.add(sub)
    db.flush()   # get sub.id

    # Add SUBMITTED audit event
    from api.workflow import _audit as _wf_audit
    _wf_audit(db, sub, "SUBMITTED", request.submitted_by,
              f"Policy submitted with {len(request.clauses)} clauses.")

    # Score every clause
    clauses    = request.clauses
    has_legal  = False
    risk_tally = {"Low": 0, "Medium": 0, "High": 0}

    for i, clause_text in enumerate(clauses):
        context = clauses[max(0, i - 2):i] + clauses[i + 1:min(len(clauses), i + 3)]
        try:
            res = predictor.predict(clause_text, policy_meta, context_clauses=context)
        except Exception as exc:
            res = {
                "risk_label": "High", "risk_score": 0.0, "confidence": 0.0,
                "lr_probabilities": {}, "flags": ["prediction_error"],
                "state_flags": [], "rewritten_clause": None,
            }

        rl = res["risk_label"]
        risk_tally[rl] = risk_tally.get(rl, 0) + 1

        sf = res.get("state_flags", [])
        if any(f["severity"] == "HIGH" for f in sf):
            has_legal = True

        final_text    = res["rewritten_clause"] or clause_text
        was_rewritten = res["rewritten_clause"] is not None

        cr = ClauseRecord(
            submission_id=sub.id,
            index=i,
            original_text=clause_text,
            final_text=final_text,
            risk_label=rl,
            risk_score=res["risk_score"],
            confidence=res["confidence"],
            flags=json.dumps(res.get("flags", [])),
            state_flags=json.dumps(sf),
            was_rewritten=was_rewritten,
        )
        db.add(cr)

    db.flush()

    # Advance workflow state
    mark_ai_assessed(db, sub, risk_tally, sum(
        len(json.loads(c.state_flags or "[]")) for c in sub.clauses
    ))
    route_after_assessment(db, sub, has_legal_flags=has_legal)
    db.commit()

    return _submission_out(sub)


# ── Workflow: list submissions ────────────────────────────────────────────────

@app.get("/submissions", response_model=list[SubmissionOut], tags=["Workflow"])
def list_submissions(
    state_filter: Optional[str] = Query(None, alias="state"),
    db: Session = Depends(get_db),
):
    q = db.query(PolicySubmission).order_by(PolicySubmission.submitted_at.desc())
    if state_filter:
        q = q.filter(PolicySubmission.workflow_state == state_filter.upper())
    return [_submission_out(s) for s in q.limit(200).all()]


# ── Workflow: single submission detail ────────────────────────────────────────

@app.get("/submissions/{submission_id}", response_model=SubmissionOut, tags=["Workflow"])
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    sub = db.query(PolicySubmission).filter(PolicySubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _submission_out(sub)


# ── Workflow: per-clause reviewer decision ────────────────────────────────────

@app.post("/submissions/{submission_id}/clauses/{clause_id}/decide",
          tags=["Workflow"])
def decide_clause(
    submission_id: int,
    clause_id:     int,
    body:          ClauseDecision,
    db:            Session = Depends(get_db),
):
    clause = db.query(ClauseRecord).filter(
        ClauseRecord.id == clause_id,
        ClauseRecord.submission_id == submission_id,
    ).first()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")

    if body.decision == "OVERRIDDEN" and body.override_text:
        clause.final_text = body.override_text

    clause_decision(db, clause, body.decision, body.actor, body.note)
    db.commit()
    return {"status": "ok", "clause_id": clause_id, "decision": body.decision}


# ── Workflow: legal approve ───────────────────────────────────────────────────

@app.post("/submissions/{submission_id}/legal-approve", tags=["Workflow"])
def legal_approve_route(
    submission_id: int,
    note:          str = "",
    actor:         str = "legal_team",
    db:            Session = Depends(get_db),
):
    sub = db.query(PolicySubmission).filter(PolicySubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        legal_approve(db, sub, actor=actor, note=note)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "new_state": sub.workflow_state}


# ── Workflow: finalize (UW approve/reject) ────────────────────────────────────

@app.post("/submissions/{submission_id}/finalize", tags=["Workflow"])
def finalize_submission(
    submission_id: int,
    body:          FinalizeRequest,
    db:            Session = Depends(get_db),
):
    sub = db.query(PolicySubmission).filter(PolicySubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    try:
        finalize(db, sub, body.decision, body.actor, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "new_state": sub.workflow_state}


# ── Queues ────────────────────────────────────────────────────────────────────

@app.get("/queue/legal", response_model=list[QueueItem], tags=["Queues"])
def legal_queue(db: Session = Depends(get_db)):
    subs = get_legal_queue(db)
    return [_queue_item(s, db) for s in subs]


@app.get("/queue/uw", response_model=list[QueueItem], tags=["Queues"])
def uw_queue(db: Session = Depends(get_db)):
    subs = get_uw_queue(db)
    return [_queue_item(s, db) for s in subs]


def _queue_item(sub: PolicySubmission, db: Session) -> QueueItem:
    risk_summary: dict = {"Low": 0, "Medium": 0, "High": 0}
    state_flag_count   = 0
    for c in sub.clauses:
        risk_summary[c.risk_label] = risk_summary.get(c.risk_label, 0) + 1
        state_flag_count += len(json.loads(c.state_flags or "[]"))
    return QueueItem(
        id=sub.id,
        policy_type=sub.policy_type,
        state=sub.state,
        workflow_state=sub.workflow_state,
        submitted_at=sub.submitted_at,
        submitted_by=sub.submitted_by,
        risk_summary=risk_summary,
        state_flag_count=state_flag_count,
    )


# ── Audit log ─────────────────────────────────────────────────────────────────

@app.get("/audit-log", response_model=list[AuditEventOut], tags=["Audit"])
def audit_log(
    submission_id: Optional[int] = None,
    limit:         int = Query(100, le=500),
    db:            Session = Depends(get_db),
):
    q = db.query(AuditEvent).order_by(AuditEvent.created_at.desc())
    if submission_id:
        q = q.filter(AuditEvent.submission_id == submission_id)
    events = q.limit(limit).all()
    return [AuditEventOut(
        id=e.id,
        submission_id=e.submission_id,
        event_type=e.event_type,
        actor=e.actor,
        description=e.description,
        payload=e.payload,
        created_at=e.created_at,
    ) for e in events]


# ── PDF upload ────────────────────────────────────────────────────────────────

@app.post("/upload-pdf", tags=["Utilities"])
async def upload_pdf(file: UploadFile = File(...)):
    """
    Upload a PDF policy document.
    Returns extracted clauses as a list of strings, ready to pass to /submit-policy.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB).")

    try:
        from api.pdf_extractor import extract_clauses_from_pdf
        clauses = extract_clauses_from_pdf(BytesIO(content))
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="pdfplumber not installed. Run: pip install pdfplumber"
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"filename": file.filename, "clause_count": len(clauses), "clauses": clauses}
