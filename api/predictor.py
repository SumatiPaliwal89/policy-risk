"""
api/predictor.py
Loads model artifacts and runs inference.

Classifier : TF-IDF → Logistic Regression → SVM  (trained model, unchanged)
Rewriter   : Gemini 2.5 Flash  (context-aware, insurance-specialist prompt)

Set GEMINI_API_KEY in .env at project root.
"""

import os
import re
import json
import joblib
import numpy as np
import pandas as pd
from scipy.sparse import hstack, csr_matrix
from dotenv import load_dotenv

from api.state_rules import check_state_rules, escalate_risk

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Paths ────────────────────────────────────────────────────────────────────
_BASE      = os.path.dirname(os.path.abspath(__file__))
_ARTIFACTS = os.path.join(_BASE, "..", "model", "artifacts")

# ── Risk flag patterns ────────────────────────────────────────────────────────
RISK_FLAG_PATTERNS = {
    "ambiguous_trigger":  r"\b(may|might|could)\b.{0,40}(apply|cover|compensate)",
    "insurer_discretion": r"insurer.{0,25}(sole|absolute|reasonable)?\s*discretion",
    "blanket_exclusion":  r"(does not cover|no coverage|excluded from coverage|not covered)",
    "conditional_payout": r"(subject to|depending on|provided that|contingent on)",
    "vague_conditions":   r"(certain (situations|circumstances)|relevant circumstances|internal evaluation)",
    "unilateral_change":  r"(reserves? the right|may (modify|amend|change|terminate))",
}

FLAG_DESCRIPTIONS = {
    "ambiguous_trigger":  "uses 'may/might' instead of a definitive commitment",
    "insurer_discretion": "gives the insurer sole discretion to decide outcomes",
    "blanket_exclusion":  "contains a broad exclusion of coverage",
    "conditional_payout": "makes payout conditional on insurer-controlled factors",
    "vague_conditions":   "uses vague language with no clear trigger conditions",
    "unilateral_change":  "allows the insurer to change terms without consent",
}


def _get_flags(clause: str) -> list:
    return [
        flag for flag, pattern in RISK_FLAG_PATTERNS.items()
        if re.search(pattern, clause, re.IGNORECASE)
    ]


class Predictor:
    def __init__(self):
        self.tfidf = joblib.load(os.path.join(_ARTIFACTS, "tfidf_vectorizer.pkl"))
        self.lr    = joblib.load(os.path.join(_ARTIFACTS, "lr_model.pkl"))
        self.svm   = joblib.load(os.path.join(_ARTIFACTS, "svm_model.pkl"))
        self.le    = joblib.load(os.path.join(_ARTIFACTS, "label_encoder.pkl"))
        sp_path    = os.path.join(_ARTIFACTS, "struct_preprocessor.pkl")
        self.sp    = joblib.load(sp_path) if os.path.exists(sp_path) else None

        self._gemini = None
        self._load_gemini()

    def _load_gemini(self):
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            print("GEMINI_API_KEY not set — rewriting unavailable.")
            return
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self._gemini = genai.GenerativeModel("gemini-2.5-flash")
            print("Gemini 2.5 Flash loaded.")
        except Exception as e:
            print(f"Gemini not loaded: {e}")

    def _rewrite(
        self,
        clause: str,
        risk_label: str,
        flags: list,
        state_flags: list,
        policy_type: str = "auto",
        state: str = "TX",
        context_clauses: list[str] | None = None,
    ) -> str | None:
        """
        Context-aware rewrite using Gemini 2.5 Flash.

        Passes surrounding clauses so Gemini understands the full policy context
        and avoids creating internal contradictions when rewriting a single clause.
        """
        if self._gemini is None:
            return None

        # Build explanatory strings for what's wrong
        flag_reasons = "; ".join(
            FLAG_DESCRIPTIONS.get(f, f) for f in flags
        ) if flags else "vague or ambiguous language"

        state_rule_reasons = ""
        if state_flags:
            state_rule_reasons = "\n".join(
                f"  - [{f['flag_id']}] {f['description']}" for f in state_flags
            )
            state_rule_reasons = f"\nRegulatory issues flagged for {state}:\n{state_rule_reasons}"

        # Include neighboring clauses for context
        context_block = ""
        if context_clauses:
            lines = "\n".join(f"  • {c}" for c in context_clauses[:5])
            context_block = f"\nFor context, these are neighboring clauses in the same policy:\n{lines}\n"

        prompt = f"""You are a senior insurance policy attorney drafting on behalf of the policyholder.

Policy type: {policy_type.upper()}
Jurisdiction: {state.upper()}

This clause was classified as {risk_label} risk because it {flag_reasons}.{state_rule_reasons}
{context_block}
Rewrite the clause below so that it:
1. Uses definitive language — "will" / "shall" instead of "may" / "might" / "could"
2. Specifies exact, objective, and measurable conditions for coverage (no "certain circumstances")
3. Removes insurer-sole-discretion language
4. Includes a specific timeframe for any actions (e.g. "within 30 calendar days")
5. Is compliant with {state} insurance regulations
6. Does not contradict the neighboring clauses shown above
7. Is written at a Grade 8 reading level — plain English, no legalese

Original clause:
{clause}

Return ONLY the rewritten clause text. No labels, no preamble, no explanation."""

        try:
            response  = self._gemini.generate_content(prompt)
            rewritten = response.text.strip().strip('"').strip("'")
            return rewritten if rewritten != clause else None
        except Exception as e:
            print(f"Gemini rewrite error: {e}")
            return None

    # ── Core inference ────────────────────────────────────────────────────────
    def predict(
        self,
        clause: str,
        policy_meta: dict,
        context_clauses: list[str] | None = None,
    ) -> dict:
        vec  = self.tfidf.transform([clause])
        lr_p = self.lr.predict_proba(vec)

        if self.sp is not None:
            meta_df = pd.DataFrame([policy_meta])
            s_vec   = self.sp.transform(meta_df)
            if not hasattr(s_vec, "toarray"):
                s_vec = csr_matrix(s_vec)
            combined = hstack([vec, csr_matrix(lr_p), s_vec])
        else:
            combined = hstack([vec, csr_matrix(lr_p)])

        label_idx   = self.svm.predict(combined)[0]
        label_proba = self.svm.predict_proba(combined)[0]
        risk_label  = self.le.inverse_transform([label_idx])[0]
        flags       = _get_flags(clause)

        # State-specific rule overlay
        state_flags  = check_state_rules(
            clause,
            state=policy_meta.get("state", "TX"),
            policy_type=policy_meta.get("policy_type", "auto"),
        )
        final_label  = escalate_risk(risk_label, state_flags)

        rewritten = (
            self._rewrite(
                clause, final_label, flags, state_flags,
                policy_type=policy_meta.get("policy_type", "auto"),
                state=policy_meta.get("state", "TX"),
                context_clauses=context_clauses,
            )
            if final_label in ("High", "Medium") else None
        )

        return {
            "risk_label":       final_label,
            "risk_score":       round(float(label_proba[label_idx]), 4),
            "confidence":       round(float(label_proba.max()), 4),
            "lr_probabilities": {
                cls: round(float(p), 4)
                for cls, p in zip(self.le.classes_, lr_p[0])
            },
            "flags":            flags,
            "state_flags":      state_flags,
            "rewritten_clause": rewritten,
        }

    # ── Full policy generation ────────────────────────────────────────────────
    def generate_policy(self, clauses: list[str], policy_meta: dict) -> dict:
        results      = []
        risk_summary = {"Low": 0, "Medium": 0, "High": 0}

        for i, clause in enumerate(clauses):
            # Provide surrounding clauses as context (2 before, 2 after)
            context = (
                clauses[max(0, i - 2):i] +
                clauses[i + 1:min(len(clauses), i + 3)]
            )
            assessment = self.predict(clause, policy_meta, context_clauses=context)
            risk_label = assessment["risk_label"]
            risk_summary[risk_label] = risk_summary.get(risk_label, 0) + 1

            final         = assessment["rewritten_clause"] or clause
            was_rewritten = assessment["rewritten_clause"] is not None

            results.append({
                "index":         i,
                "original":      clause,
                "final":         final,
                "risk_label":    risk_label,
                "confidence":    assessment["confidence"],
                "flags":         assessment["flags"],
                "state_flags":   assessment["state_flags"],
                "was_rewritten": was_rewritten,
            })

        generated_policy = "\n\n".join(
            f"{i+1}. {r['final']}" for i, r in enumerate(results)
        )

        high_not_rewritten = sum(
            1 for r in results
            if r["risk_label"] == "High" and not r["was_rewritten"]
        )

        return {
            "risk_summary":     risk_summary,
            "rewritten_count":  sum(1 for r in results if r["was_rewritten"]),
            "clauses":          results,
            "generated_policy": generated_policy,
            "ready_for_review": high_not_rewritten == 0,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
    return _predictor
