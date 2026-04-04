"""
api/state_rules.py
State-specific insurance regulation overlays.

Each state entry maps policy_type → list of rule checks.
A rule check receives the clause text and returns a flag string or None.
"""

import re
from typing import Callable, Optional

# Each rule: (flag_id, description, check_fn)
Rule = tuple[str, str, Callable[[str], bool]]


def _has(pattern: str) -> Callable[[str], bool]:
    return lambda clause: bool(re.search(pattern, clause, re.IGNORECASE))


def _missing(pattern: str) -> Callable[[str], bool]:
    return lambda clause: not bool(re.search(pattern, clause, re.IGNORECASE))


# ── State rule definitions ────────────────────────────────────────────────────

_STATE_RULES: dict[str, list[Rule]] = {

    # California — Prop 103, DOI strict consumer-protection regime
    "CA": [
        (
            "CA_PRIOR_APPROVAL",
            "CA Prop 103: rate/coverage changes require prior DOI approval — "
            "clause appears to allow unilateral modification",
            _has(r"reserves? the right|may (modify|change|amend|alter)")
        ),
        (
            "CA_FAIR_CLAIMS",
            "CA Fair Claims Settlement Practices: must specify a definite settlement timeline",
            lambda clause: (
                bool(re.search(r"(claim|loss|damage)", clause, re.IGNORECASE)) and
                not bool(re.search(r"\d+\s*(day|business day|calendar day)", clause, re.IGNORECASE))
            )
        ),
        (
            "CA_PLAIN_LANGUAGE",
            "CA Insurance Code §756: consumer policies must use plain language — "
            "legalese density is high",
            lambda clause: len(re.findall(
                r"\b(notwithstanding|aforementioned|hereinafter|indemnification"
                r"|subrogation|ipso facto)\b", clause, re.IGNORECASE)) >= 2
        ),
    ],

    # New York — DFS 11 NYCRR 216 prompt-pay, strict exclusion disclosure
    "NY": [
        (
            "NY_EXCLUSION_DISCLOSURE",
            "NY DFS 11 NYCRR 216: all exclusions must be explicitly and clearly stated",
            lambda clause: (
                bool(re.search(r"(not cover|exclud|no coverage|except)", clause, re.IGNORECASE)) and
                bool(re.search(r"\b(certain|some|various|relevant)\b", clause, re.IGNORECASE))
            )
        ),
        (
            "NY_PROMPT_PAY",
            "NY Insurance Law §3224-a: undisputed claims must be paid within 30 days — "
            "clause lacks a timeframe",
            lambda clause: (
                bool(re.search(r"(pay|reimburse|compensate)", clause, re.IGNORECASE)) and
                not bool(re.search(r"\d+\s*day", clause, re.IGNORECASE))
            )
        ),
        (
            "NY_DISCRIMINATION",
            "NY Exec Law §296: clause may create discriminatory classification risk",
            _has(r"\b(age|gender|race|national origin|marital status)\b")
        ),
    ],

    # Florida — Citizens Property Insurance, assignment of benefits rules
    "FL": [
        (
            "FL_AOB",
            "FL SB 2-D (2022): assignment-of-benefits clauses require specific consumer disclosures",
            _has(r"(assignment|assign|transfer).{0,30}(benefit|claim|right|proceed)")
        ),
        (
            "FL_HURRICANE",
            "FL Statute §627.701: hurricane deductible must be clearly stated separately",
            lambda clause: (
                bool(re.search(r"(hurricane|wind|storm|named storm)", clause, re.IGNORECASE)) and
                not bool(re.search(r"hurricane deductible", clause, re.IGNORECASE))
            )
        ),
        (
            "FL_INFLATION",
            "FL Citizens: replacement cost clauses must include inflation guard language",
            lambda clause: (
                bool(re.search(r"replacement cost", clause, re.IGNORECASE)) and
                not bool(re.search(r"inflation", clause, re.IGNORECASE))
            )
        ),
    ],

    # Texas — TDI, prompt-pay statute, plain meaning doctrine
    "TX": [
        (
            "TX_PROMPT_PAY",
            "TX Insurance Code §542: carrier must acknowledge claim within 15 days — "
            "clause lacks an acknowledgment commitment",
            lambda clause: (
                bool(re.search(r"(claim|loss)", clause, re.IGNORECASE)) and
                bool(re.search(r"(sole discretion|internal evaluation|as determined by)", clause, re.IGNORECASE))
            )
        ),
        (
            "TX_PLAIN_MEANING",
            "TX plain meaning doctrine: ambiguous terms are construed against the insurer",
            _has(r"\b(may|might|could)\b.{0,30}(cover|apply|pay|compensate)")
        ),
        (
            "TX_CANCELLATION",
            "TX Insurance Code §551: cancellation notice must specify grounds and timeline",
            lambda clause: (
                bool(re.search(r"(cancel|terminat)", clause, re.IGNORECASE)) and
                not bool(re.search(r"\d+\s*(day|notice)", clause, re.IGNORECASE))
            )
        ),
    ],
}

# Generic federal / NAIC model act rules applied to all states
_FEDERAL_RULES: list[Rule] = [
    (
        "NAIC_GRACE_PERIOD",
        "NAIC Model Act: life/health policies must include a grace period provision",
        lambda clause: (
            bool(re.search(r"(terminat|lapse|expir)", clause, re.IGNORECASE)) and
            not bool(re.search(r"grace period", clause, re.IGNORECASE))
        )
    ),
    (
        "NAIC_CONCEALMENT",
        "NAIC Concealment clause: voids coverage based on undisclosed facts — "
        "ensure proportionality to materiality",
        _has(r"(concealment|material (fact|misrepresentation)|void.{0,20}policy)")
    ),
]


# ── Public API ────────────────────────────────────────────────────────────────

def check_state_rules(clause: str, state: str, policy_type: str = "auto") -> list[dict]:
    """
    Returns a list of triggered state-rule flags for the given clause.

    Each flag is a dict with keys: flag_id, description, state, severity.
    """
    triggered = []
    rules = _STATE_RULES.get(state.upper(), []) + _FEDERAL_RULES

    for flag_id, description, check_fn in rules:
        try:
            if check_fn(clause):
                triggered.append({
                    "flag_id":     flag_id,
                    "description": description,
                    "state":       state.upper(),
                    "severity":    "HIGH" if flag_id.endswith(("_PRIOR_APPROVAL", "_AOB",
                                                                "_EXCLUSION_DISCLOSURE"))
                                          else "MEDIUM",
                })
        except Exception:
            pass

    return triggered


def escalate_risk(base_risk: str, state_flags: list[dict]) -> str:
    """
    If state rules fire, escalate the model risk label by one level (max High).
    """
    if not state_flags:
        return base_risk
    high_state_flags = [f for f in state_flags if f["severity"] == "HIGH"]
    if high_state_flags and base_risk == "Low":
        return "Medium"
    if state_flags and base_risk == "Medium":
        return "High"
    return base_risk
