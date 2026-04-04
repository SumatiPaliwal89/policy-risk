"""
integration/guidewire_hook_simulation.py

Simulates how Guidewire PolicyCenter would call the risk assessment API
from a GOSU plugin during the underwriting workflow.

In real PolicyCenter the equivalent GOSU code lives at:
  modules/configuration/gsrc/gw/plugin/RiskAssessmentPlugin.gs

The GOSU plugin would look like:

    package gw.plugin

    uses gw.api.webservice.HttpClient
    uses gw.lang.reflect.json.JsonObject

    class RiskAssessmentPlugin {

      static var API_URL = "http://risk-api:8000"

      static function assessClause(clause: String, period: PolicyPeriod): String {
        var client = new HttpClient()
        var body = new JsonObject()
        body.put("clause", clause)
        body.put("policy_type", period.Lines.first().PatternCode)
        body.put("coverage_amount", period.TotalCostRPT.Amount as int)
        body.put("applicant_age", period.PrimaryInsuredAge)
        body.put("prior_claims_count", period.PrimaryInsured.ClaimCount)
        body.put("state", period.BaseState.Code)
        var response = client.post(API_URL + "/assess-risk", body.toJson())
        var result   = JsonObject.parse(response)

        if (result.getString("risk_label") == "High") {
          period.addNote("AI Risk Flag: " + result.get("flags").toString())
          var issue = period.UWIssues.createNew()
          issue.LongDescription = "AI detected high-risk clause: " + clause
          issue.ShortDescription = "AI Risk: High"
        }
        return result.getString("risk_label")
      }
    }

This Python file replicates the same logic against the FastAPI service.

Usage:
    python integration/guidewire_hook_simulation.py
"""

import json
import requests

API_BASE = "http://localhost:8000"


# ── Single clause assessment ─────────────────────────────────────────────
def assess_clause(clause: str, policy_meta: dict) -> dict:
    """
    Mirrors the GOSU HttpClient.post() call.
    """
    payload = {"clause": clause, **policy_meta}
    try:
        resp = requests.post(f"{API_BASE}/assess-risk", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"error": "API not running. Start with: uvicorn api.main:app --port 8000"}


# ── Underwriting workflow simulation ─────────────────────────────────────
def simulate_underwriting_workflow(policy_clauses: list[str], metadata: dict) -> dict:
    """
    Simulates the PolicyCenter underwriting review stage.

    Mirrors the workflow:
      OnApprove() → assess each clause → if High → create UWIssue
                                        → block auto-approval

    Returns a UW report matching what PolicyCenter would store on
    PolicyPeriod.ai_risk_score, ai_risk_flags, ai_rewritten_clause.
    """
    uw_issues = []
    all_results = []

    for clause in policy_clauses:
        result = assess_clause(clause, metadata)
        if "error" in result:
            print("Error:", result["error"])
            return {}

        all_results.append(result)

        if result["risk_label"] in ("High", "Medium"):
            issue = {
                # PolicyPeriod custom fields (as defined in PolicyCenter Studio)
                "ai_risk_score":        result["risk_label"],
                "ai_risk_flags":        json.dumps(result["flags"]),
                "ai_assessed_clause":   clause,
                "ai_rewritten_clause":  result.get("rewritten_clause"),
                "confidence":           result["confidence"],
                # UWIssue fields
                "uw_short_description": f"AI Risk: {result['risk_label']}",
                "uw_long_description":  (
                    f"AI flagged clause as {result['risk_label']} risk "
                    f"(confidence {result['confidence']:.0%}). "
                    f"Flags: {', '.join(result['flags']) or 'none'}."
                ),
                "block_approval":       result["risk_label"] == "High",
            }
            uw_issues.append(issue)

    risk_counts = {"Low": 0, "Medium": 0, "High": 0}
    for r in all_results:
        risk_counts[r["risk_label"]] = risk_counts.get(r["risk_label"], 0) + 1

    overall = "High" if risk_counts["High"] > 0 else (
        "Medium" if risk_counts["Medium"] > 0 else "Low"
    )

    return {
        "total_clauses":     len(policy_clauses),
        "risk_distribution": risk_counts,
        "overall_risk":      overall,
        "auto_approval":     overall == "Low",
        "uw_issues":         uw_issues,
        # These fields would be written to PolicyPeriod in real PC
        "pc_fields": {
            "ai_risk_score":    overall,
            "ai_assessed_at":   __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "ai_flagged_count": len(uw_issues),
        }
    }


# ── Demo run ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    SAMPLE_POLICY_CLAUSES = [
        "The insurer will compensate losses described in the policy when documentation confirms eligibility.",
        "Coverage may apply in certain situations subject to evaluation by the insurer.",
        "Claims may be approved or declined based on the insurer's sole interpretation of the situation.",
        "Covered damages will be reimbursed in accordance with the policy schedule.",
        "The insurer reserves the right to modify coverage terms at any time without prior notice.",
    ]

    POLICY_METADATA = {
        "policy_type":        "commercial",
        "coverage_amount":    2000000,
        "applicant_age":      52,
        "prior_claims_count": 3,
        "deductible_amount":  5000,
        "state":              "TX",
    }

    print("=" * 60)
    print("GUIDEWIRE POLICYCENTER — UNDERWRITING WORKFLOW SIMULATION")
    print("=" * 60)
    print(f"Policy type   : {POLICY_METADATA['policy_type']}")
    print(f"Coverage      : ${POLICY_METADATA['coverage_amount']:,}")
    print(f"Prior claims  : {POLICY_METADATA['prior_claims_count']}")
    print(f"Clauses to assess: {len(SAMPLE_POLICY_CLAUSES)}")
    print()

    report = simulate_underwriting_workflow(SAMPLE_POLICY_CLAUSES, POLICY_METADATA)
    if not report:
        exit(1)

    print("RISK DISTRIBUTION:")
    for label, count in report["risk_distribution"].items():
        print(f"  {label:8s}: {count}")

    print(f"\nOVERALL RISK  : {report['overall_risk']}")
    print(f"AUTO-APPROVAL : {'YES' if report['auto_approval'] else 'NO — routed to senior underwriter'}")

    if report["uw_issues"]:
        print(f"\nUW ISSUES ({len(report['uw_issues'])}):")
        for i, issue in enumerate(report["uw_issues"], 1):
            print(f"\n  Issue {i}:")
            print(f"    Clause   : {issue['ai_assessed_clause'][:80]}...")
            print(f"    Risk     : {issue['ai_risk_score']}  (confidence {issue['confidence']:.0%})")
            print(f"    Flags    : {json.loads(issue['ai_risk_flags'])}")
            if issue["ai_rewritten_clause"]:
                print(f"    Suggested: {issue['ai_rewritten_clause'][:80]}...")
            print(f"    Blocks approval: {issue['block_approval']}")

    print("\nPOLICYCENTER FIELDS TO WRITE:")
    for k, v in report["pc_fields"].items():
        print(f"  PolicyPeriod.{k} = {v}")
