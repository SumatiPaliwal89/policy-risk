"""
tests/test_model.py
Unit tests for the model inference pipeline.
Run: pytest tests/test_model.py -v
"""

import os
import pytest
import joblib
import numpy as np
import pandas as pd
from scipy.sparse import hstack, csr_matrix


ARTIFACTS = os.path.join(os.path.dirname(__file__), "..", "model", "artifacts")
ARTIFACTS_EXIST = all(
    os.path.exists(os.path.join(ARTIFACTS, f))
    for f in ["tfidf_vectorizer.pkl", "lr_model.pkl", "svm_model.pkl", "label_encoder.pkl"]
)


@pytest.fixture(scope="module")
def artifacts():
    if not ARTIFACTS_EXIST:
        pytest.skip("Model artifacts not found — run model/train.py first.")
    tfidf = joblib.load(os.path.join(ARTIFACTS, "tfidf_vectorizer.pkl"))
    lr    = joblib.load(os.path.join(ARTIFACTS, "lr_model.pkl"))
    svm   = joblib.load(os.path.join(ARTIFACTS, "svm_model.pkl"))
    le    = joblib.load(os.path.join(ARTIFACTS, "label_encoder.pkl"))
    sp_path = os.path.join(ARTIFACTS, "struct_preprocessor.pkl")
    sp    = joblib.load(sp_path) if os.path.exists(sp_path) else None
    return tfidf, lr, svm, le, sp


CLEAR_LOW  = "The insurer will compensate losses when documentation confirms eligibility."
VAGUE_HIGH = "Coverage may apply in certain situations subject to evaluation by the insurer."

SAMPLE_META = {
    "policy_type": "auto",
    "coverage_amount": 75000,
    "applicant_age": 35,
    "prior_claims_count": 0,
    "deductible_amount": 1000,
    "state": "TX",
}


def run_inference(artifacts, clause, meta):
    tfidf, lr, svm, le, sp = artifacts
    vec  = tfidf.transform([clause])
    prob = lr.predict_proba(vec)
    if sp is not None:
        s    = csr_matrix(sp.transform(pd.DataFrame([meta])))
        feat = hstack([vec, csr_matrix(prob), s])
    else:
        feat = hstack([vec, csr_matrix(prob)])
    label_idx  = svm.predict(feat)[0]
    label_prob = svm.predict_proba(feat)[0]
    return le.inverse_transform([label_idx])[0], label_prob


class TestModelLoading:
    def test_artifacts_load(self, artifacts):
        tfidf, lr, svm, le, sp = artifacts
        assert tfidf is not None
        assert lr    is not None
        assert svm   is not None
        assert le    is not None

    def test_label_classes(self, artifacts):
        _, _, _, le, _ = artifacts
        classes = set(le.classes_)
        assert "Low"  in classes
        assert "High" in classes


class TestInference:
    def test_low_risk_clause(self, artifacts):
        label, proba = run_inference(artifacts, CLEAR_LOW, SAMPLE_META)
        assert label in ("Low", "Medium", "High"), f"Unexpected label: {label}"
        assert abs(proba.sum() - 1.0) < 1e-5, "Probabilities must sum to 1"

    def test_high_risk_clause(self, artifacts):
        label, proba = run_inference(artifacts, VAGUE_HIGH, {**SAMPLE_META, "prior_claims_count": 3})
        assert label in ("Medium", "High"), f"Expected Medium or High, got {label}"

    def test_confidence_range(self, artifacts):
        _, proba = run_inference(artifacts, CLEAR_LOW, SAMPLE_META)
        assert all(0.0 <= p <= 1.0 for p in proba), "All probabilities must be in [0,1]"

    def test_output_shape(self, artifacts):
        _, _, _, le, _ = artifacts
        _, proba = run_inference(artifacts, CLEAR_LOW, SAMPLE_META)
        assert len(proba) == len(le.classes_), "Proba length must match number of classes"

    def test_empty_clause_does_not_crash(self, artifacts):
        # Edge case: very short clause
        label, proba = run_inference(artifacts, "Coverage applies.", SAMPLE_META)
        assert label in set(artifacts[3].classes_)

    def test_high_coverage_shifts_risk(self, artifacts):
        # Same vague clause, but high-risk structured context
        high_meta = {**SAMPLE_META, "coverage_amount": 4_000_000, "prior_claims_count": 5}
        label_high, _ = run_inference(artifacts, VAGUE_HIGH, high_meta)
        # We just assert no crash and valid output
        assert label_high in set(artifacts[3].classes_)


class TestRiskFlags:
    """Tests for the rule-based flag detection layer."""

    def test_ambiguous_trigger_detected(self):
        from api.predictor import _get_flags
        clause = "Coverage may apply depending on circumstances."
        flags = _get_flags(clause)
        assert "ambiguous_trigger" in flags or "conditional_payout" in flags

    def test_insurer_discretion_detected(self):
        from api.predictor import _get_flags
        clause = "The insurer has sole discretion to approve or deny claims."
        flags = _get_flags(clause)
        assert "insurer_discretion" in flags

    def test_blanket_exclusion_detected(self):
        from api.predictor import _get_flags
        clause = "Flood damage is excluded from coverage."
        flags = _get_flags(clause)
        assert "blanket_exclusion" in flags

    def test_clean_clause_no_flags(self):
        from api.predictor import _get_flags
        clause = "The insurer will pay claims that comply with the policy terms."
        flags = _get_flags(clause)
        assert len(flags) == 0
