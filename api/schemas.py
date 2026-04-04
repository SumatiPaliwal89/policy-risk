"""
api/schemas.py
Pydantic request and response models for the risk assessment API.
"""

from __future__ import annotations
from typing import Dict, List, Literal, Optional, Any
from pydantic import BaseModel, Field
import datetime


# ── Shared meta ───────────────────────────────────────────────────────────────

class PolicyMeta(BaseModel):
    policy_type:        Literal["auto", "home", "commercial", "life"] = "auto"
    coverage_amount:    int = Field(default=100000, ge=1000, le=10_000_000)
    applicant_age:      int = Field(default=35, ge=18, le=100)
    prior_claims_count: int = Field(default=0, ge=0, le=20)
    deductible_amount:  int = Field(default=1000, ge=0)
    state:              str = Field(default="TX", max_length=2)


# ── Single-clause assessment ──────────────────────────────────────────────────

class PolicyRequest(BaseModel):
    clause:             str = Field(..., min_length=10)
    policy_type:        Literal["auto", "home", "commercial", "life"] = "auto"
    coverage_amount:    int = Field(default=100000, ge=1000, le=10_000_000)
    applicant_age:      int = Field(default=35, ge=18, le=100)
    prior_claims_count: int = Field(default=0, ge=0, le=20)
    deductible_amount:  int = Field(default=1000, ge=0)
    state:              str = Field(default="TX", max_length=2)

    model_config = {
        "json_schema_extra": {
            "example": {
                "clause": "Coverage may apply in certain situations subject to evaluation by the insurer.",
                "policy_type": "commercial",
                "coverage_amount": 1500000,
                "applicant_age": 45,
                "prior_claims_count": 2,
                "deductible_amount": 5000,
                "state": "TX",
            }
        }
    }


class StateFlagOut(BaseModel):
    flag_id:     str
    description: str
    state:       str
    severity:    str


class RiskResponse(BaseModel):
    risk_label:        Literal["Low", "Medium", "High"]
    risk_score:        float
    confidence:        float
    lr_probabilities:  Dict[str, float]
    flags:             List[str]
    state_flags:       List[StateFlagOut] = []
    rewritten_clause:  Optional[str] = None


class BulkRequest(BaseModel):
    clauses: List[PolicyRequest] = Field(..., min_length=1, max_length=500)


class BulkResponse(BaseModel):
    total:   int
    flagged: int
    results: List[RiskResponse]


# ── Policy generation (legacy /generate-policy endpoint) ─────────────────────

class GeneratePolicyRequest(BaseModel):
    clauses: List[str] = Field(..., min_length=1, max_length=100)
    meta:    PolicyMeta = Field(default_factory=PolicyMeta)


class GeneratedClause(BaseModel):
    index:         int
    original:      str
    final:         str
    risk_label:    Literal["Low", "Medium", "High"]
    confidence:    float
    flags:         List[str]
    state_flags:   List[StateFlagOut] = []
    was_rewritten: bool


class GeneratePolicyResponse(BaseModel):
    risk_summary:     Dict[str, int]
    rewritten_count:  int
    clauses:          List[GeneratedClause]
    generated_policy: str
    ready_for_review: bool


# ── Workflow: submit a policy ─────────────────────────────────────────────────

class SubmissionRequest(BaseModel):
    clauses:      List[str]  = Field(..., min_length=1, max_length=100,
                                     description="Policy clause texts, in order.")
    meta:         PolicyMeta = Field(default_factory=PolicyMeta)
    submitted_by: str        = Field(default="underwriter")


class ClauseOut(BaseModel):
    id:            int
    index:         int
    original_text: str
    final_text:    str
    risk_label:    str
    risk_score:    float
    confidence:    float
    flags:         List[str]
    state_flags:   List[Any]
    was_rewritten: bool
    decision:      str
    reviewer_note: str

    model_config = {"from_attributes": True}


class SubmissionOut(BaseModel):
    id:                 int
    policy_type:        str
    state:              str
    coverage_amount:    int
    applicant_age:      int
    prior_claims_count: int
    deductible_amount:  int
    workflow_state:     str
    submitted_at:       datetime.datetime
    updated_at:         datetime.datetime
    submitted_by:       str
    notes:              str
    risk_summary:       Dict[str, int] = {}
    state_flag_count:   int = 0
    clauses:            List[ClauseOut] = []
    generated_policy:   str = ""

    model_config = {"from_attributes": True}


# ── Per-clause reviewer decision ──────────────────────────────────────────────

class ClauseDecision(BaseModel):
    decision:      Literal["ACCEPTED", "OVERRIDDEN"]
    override_text: Optional[str] = None   # provide when OVERRIDDEN
    note:          str = ""
    actor:         str = "underwriter"


# ── Finalize submission ───────────────────────────────────────────────────────

class FinalizeRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    note:     str = ""
    actor:    str = "underwriter"


# ── Audit log ────────────────────────────────────────────────────────────────

class AuditEventOut(BaseModel):
    id:            int
    submission_id: int
    event_type:    str
    actor:         str
    description:   str
    payload:       str
    created_at:    datetime.datetime

    model_config = {"from_attributes": True}


# ── Queue item (list view) ────────────────────────────────────────────────────

class QueueItem(BaseModel):
    id:               int
    policy_type:      str
    state:            str
    workflow_state:   str
    submitted_at:     datetime.datetime
    submitted_by:     str
    risk_summary:     Dict[str, int] = {}
    state_flag_count: int = 0

    model_config = {"from_attributes": True}
