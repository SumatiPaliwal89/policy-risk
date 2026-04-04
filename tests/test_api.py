"""
tests/test_api.py
Integration tests for the FastAPI endpoints.
Run: pytest tests/test_api.py -v
(Requires model artifacts. API is tested via TestClient — no server needed.)
"""

import os
import pytest

# Skip entire module if artifacts are missing
ARTIFACTS = os.path.join(os.path.dirname(__file__), "..", "model", "artifacts")
if not os.path.exists(os.path.join(ARTIFACTS, "svm_model.pkl")):
    pytest.skip("Model artifacts not found — run model/train.py first.", allow_module_level=True)

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

VALID_PAYLOAD = {
    "clause":             "Coverage may apply in certain situations subject to evaluation by the insurer.",
    "policy_type":        "commercial",
    "coverage_amount":    1500000,
    "applicant_age":      45,
    "prior_claims_count": 2,
    "deductible_amount":  5000,
    "state":              "TX",
}

LOW_PAYLOAD = {
    "clause":             "The insurer will compensate losses when documentation confirms eligibility.",
    "policy_type":        "auto",
    "coverage_amount":    75000,
    "applicant_age":      30,
    "prior_claims_count": 0,
    "deductible_amount":  1000,
    "state":              "CA",
}


class TestHealth:
    def test_health_returns_ok(self):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_health_has_model_version(self):
        res = client.get("/health")
        assert "model_version" in res.json()


class TestAssessRisk:
    def test_valid_request_200(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        assert res.status_code == 200

    def test_response_has_required_fields(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        data = res.json()
        for field in ["risk_label", "risk_score", "confidence", "lr_probabilities", "flags"]:
            assert field in data, f"Missing field: {field}"

    def test_risk_label_is_valid(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        assert res.json()["risk_label"] in ("Low", "Medium", "High")

    def test_confidence_in_range(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        conf = res.json()["confidence"]
        assert 0.0 <= conf <= 1.0

    def test_risk_score_in_range(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        score = res.json()["risk_score"]
        assert 0.0 <= score <= 1.0

    def test_lr_probabilities_sum_to_one(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        probs = res.json()["lr_probabilities"]
        total = sum(probs.values())
        assert abs(total - 1.0) < 0.01

    def test_flags_is_list(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        assert isinstance(res.json()["flags"], list)

    def test_low_risk_clause(self):
        res = client.post("/assess-risk", json=LOW_PAYLOAD)
        assert res.status_code == 200
        assert res.json()["risk_label"] in ("Low", "Medium", "High")

    def test_rewritten_clause_only_on_high(self):
        res = client.post("/assess-risk", json=VALID_PAYLOAD)
        data = res.json()
        if data["risk_label"] == "High":
            # rewritten_clause may be present (None if BART not loaded)
            assert "rewritten_clause" in data
        else:
            assert data.get("rewritten_clause") is None

    def test_missing_clause_returns_422(self):
        res = client.post("/assess-risk", json={"policy_type": "auto"})
        assert res.status_code == 422

    def test_short_clause_returns_422(self):
        res = client.post("/assess-risk", json={**VALID_PAYLOAD, "clause": "short"})
        assert res.status_code == 422

    def test_invalid_policy_type_returns_422(self):
        res = client.post("/assess-risk", json={**VALID_PAYLOAD, "policy_type": "boat"})
        assert res.status_code == 422

    def test_negative_coverage_returns_422(self):
        res = client.post("/assess-risk", json={**VALID_PAYLOAD, "coverage_amount": -1})
        assert res.status_code == 422

    def test_defaults_applied_when_optional_fields_omitted(self):
        res = client.post("/assess-risk", json={"clause": VALID_PAYLOAD["clause"]})
        assert res.status_code == 200


class TestBulkAssess:
    def test_bulk_valid(self):
        res = client.post("/bulk-assess", json={"clauses": [VALID_PAYLOAD, LOW_PAYLOAD]})
        assert res.status_code == 200

    def test_bulk_response_structure(self):
        res = client.post("/bulk-assess", json={"clauses": [VALID_PAYLOAD, LOW_PAYLOAD]})
        data = res.json()
        assert data["total"] == 2
        assert "flagged" in data
        assert len(data["results"]) == 2

    def test_bulk_empty_returns_422(self):
        res = client.post("/bulk-assess", json={"clauses": []})
        assert res.status_code == 422

    def test_bulk_all_results_have_risk_label(self):
        payload = {"clauses": [VALID_PAYLOAD, LOW_PAYLOAD, VALID_PAYLOAD]}
        res  = client.post("/bulk-assess", json=payload)
        data = res.json()
        for result in data["results"]:
            assert result["risk_label"] in ("Low", "Medium", "High")
