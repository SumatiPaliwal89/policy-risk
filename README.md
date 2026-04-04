# AI-Based Risk Assessment for New Policies in Guidewire PolicyCenter

An end-to-end AI system that reads insurance policy clauses, scores them for risk using a trained ML model, applies state-specific regulatory rules, rewrites risky language using Gemini 2.5 Flash, and routes every submission through a structured underwriting workflow — all integrated with a full audit trail.

---

## What Problem This Solves

When an insurance company writes a new policy, an underwriter has to manually read every clause and check whether the language is:
- Ambiguous ("coverage *may* apply in *certain* situations")
- Insurer-favoring (sole discretion clauses, blanket exclusions)
- Non-compliant with state regulations (CA Prop 103, NY DFS, FL AOB rules, TX prompt-pay)

This process is slow, inconsistent, and expensive. A senior underwriter's time is wasted on boilerplate that a model can flag in milliseconds.

**This system eliminates that manual work.** The underwriter sees a clean, AI-rewritten policy, reviews it once, and clicks Approve.

---

## Architecture

```
PDF / Text Input
       │
       ▼
┌─────────────────────────────────────────┐
│           FastAPI Microservice           │
│                                         │
│  TF-IDF → Logistic Regression           │
│      ↓                                  │
│  + Structured features                  │
│  (policy type, coverage, state, age…)   │
│      ↓                                  │
│  SVM Meta-Classifier                    │
│      ↓                                  │
│  Risk Label: Low / Medium / High        │
│      ↓                                  │
│  State Rules Overlay (CA/NY/FL/TX)      │
│      ↓                                  │
│  Gemini 2.5 Flash Rewriter              │
│  (context-aware, state-specific)        │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│        Workflow State Machine           │
│                                         │
│  SUBMITTED → AI_ASSESSED                │
│      ↓                ↓                 │
│  LEGAL_REVIEW    UW_REVIEW              │
│      ↓                ↓                 │
│  APPROVED / REJECTED                    │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│  SQLite Audit Trail (every event logged) │
└─────────────────────────────────────────┘
```

---

## Project Structure

```
guidewire/
│
├── api/                          ← FastAPI backend
│   ├── main.py                   ← all endpoints
│   ├── predictor.py              ← ML inference + Gemini rewriter
│   ├── schemas.py                ← Pydantic request/response models
│   ├── database.py               ← SQLAlchemy ORM (SQLite)
│   ├── state_rules.py            ← CA / NY / FL / TX regulation checks
│   ├── pdf_extractor.py          ← PDF → clause list (pdfplumber)
│   ├── workflow.py               ← state machine transitions
│   └── requirements.txt
│
├── model/
│   ├── train.py                  ← training script (CLI)
│   ├── evaluate.py               ← evaluation + metrics
│   └── artifacts/                ← trained .pkl files (gitignored)
│       ├── tfidf_vectorizer.pkl
│       ├── lr_model.pkl
│       ├── svm_model.pkl
│       ├── label_encoder.pkl
│       └── struct_preprocessor.pkl
│
├── notebooks/
│   ├── 01_data_augmentation.ipynb   ← builds augmented_dataset.csv
│   └── 02_model_v2.ipynb            ← trains model, saves artifacts
│
├── frontend/
│   ├── index.html                ← 4-tab UI
│   ├── app.js                    ← all frontend logic
│   └── style.css                 ← full styling
│
├── integration/
│   └── guidewire_hook_simulation.py  ← simulates GOSU plugin call
│
├── tests/
│   ├── test_api.py
│   └── test_model.py
│
├── data/
│   └── guidewire.db              ← SQLite database (auto-created)
│
├── .env                          ← GEMINI_API_KEY (gitignored)
├── dataset-guidewire_sample.csv  ← original 5,000-clause dataset
└── augmented_dataset.csv         ← 15,425-clause 3-class dataset (gitignored)
```

---

## The Dataset

### Original (`dataset-guidewire_sample.csv`)
- 5,000 policy clauses
- Binary labels: Low / High
- Synthetic, balanced

### Augmented (`augmented_dataset.csv`)
Built in `notebooks/01_data_augmentation.ipynb` from three sources:

| Source | Rows | How |
|---|---|---|
| Original dataset | ~5,000 | Relabeled into 3 classes by LR confidence score |
| UNFAIR-ToS (LexGLUE) | ~5,000 | Real legal text — unfair clauses → High, fair → Low |
| CUAD (Contract NLU) | ~3,000 | Contract clauses from 500 real agreements |
| Hedging injection | ~2,500 | Low-risk clauses with ambiguous words injected → Medium |

**Final: 15,425 rows, 3 classes — Low: 10,435 · High: 2,500 · Medium: 2,490**

**9 columns:** `clause`, `risk`, `source`, `policy_type`, `coverage_amount`, `applicant_age`, `prior_claims_count`, `deductible_amount`, `state`

---

## The ML Model

### Pipeline (same architecture as original `Guidewire_Project.ipynb`, extended)

```
Clause text
    │
    ▼
TF-IDF Vectorizer (max 10,000 features, unigrams + bigrams)
    │
    ▼
Logistic Regression (text signal — produces class probabilities)
    │
    ├── LR probabilities (3 floats)
    │
Structured features (policy_type, coverage_amount, age, claims, deductible, state)
    │── OneHotEncoder + StandardScaler via ColumnTransformer
    │
    ▼
hstack([tfidf_vec, lr_proba, structured_vec])  ← combined feature matrix
    │
    ▼
SVM (RBF kernel, CalibratedClassifierCV) ← final classifier
    │
    ▼
Risk Label + Probability
```

### Training results

| Metric | Value |
|---|---|
| Overall accuracy | 97.31% |
| High F1 | 1.00 |
| Low F1 | 0.98 |
| Medium F1 | 0.91 (recall 0.88) |

Medium recall (0.88) is the known weak spot — Medium clauses are intentionally ambiguous by construction, making them harder to classify. High-risk recall is 1.00, which is the critical metric for underwriting.

### Training a new model

```bash
python model/train.py --data augmented_dataset.csv
python model/evaluate.py
```

---

## The API

Start with:
```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Interactive docs: **http://localhost:8000/docs**

### Endpoints

#### Assessment (stateless, no DB)

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/health` | Status check |
| POST | `/assess-risk` | Score a single clause |
| POST | `/bulk-assess` | Score up to 500 clauses |
| POST | `/generate-policy` | Score + rewrite all clauses, return clean policy text |

**`POST /assess-risk` request:**
```json
{
  "clause": "Coverage may apply in certain situations subject to the insurer's sole discretion.",
  "policy_type": "commercial",
  "coverage_amount": 1500000,
  "applicant_age": 45,
  "prior_claims_count": 2,
  "deductible_amount": 5000,
  "state": "CA"
}
```

**Response:**
```json
{
  "risk_label": "High",
  "risk_score": 0.91,
  "confidence": 0.91,
  "lr_probabilities": { "Low": 0.03, "Medium": 0.06, "High": 0.91 },
  "flags": ["ambiguous_trigger", "insurer_discretion"],
  "state_flags": [
    {
      "flag_id": "CA_PRIOR_APPROVAL",
      "description": "CA Prop 103: rate/coverage changes require prior DOI approval...",
      "state": "CA",
      "severity": "HIGH"
    }
  ],
  "rewritten_clause": "The insurer shall provide coverage for all losses described in this policy. Coverage shall apply when the policyholder submits a written claim with supporting documentation within 30 calendar days of the loss event."
}
```

#### Workflow (persistent, uses SQLite)

| Method | Endpoint | What it does |
|---|---|---|
| POST | `/submit-policy` | Submit clauses → AI scores all → routes to queue |
| GET | `/submissions` | List all submissions |
| GET | `/submissions/{id}` | Full submission with all clauses |
| POST | `/submissions/{id}/clauses/{cid}/decide` | Accept or override a clause |
| POST | `/submissions/{id}/legal-approve` | Legal team clears state flags |
| POST | `/submissions/{id}/finalize` | UW approves or rejects |
| GET | `/queue/uw` | Underwriter review queue |
| GET | `/queue/legal` | Legal review queue |
| GET | `/audit-log` | Full audit trail |
| POST | `/upload-pdf` | PDF → extracted clauses |

---

## Risk Flags (Explainability Layer)

On top of the ML model, a rule-based layer detects specific language patterns. This makes the output explainable — underwriters need to know *why* a clause is risky, not just that it is.

| Flag | Pattern | Meaning |
|---|---|---|
| `ambiguous_trigger` | "may/might/could … apply/cover" | Commitment is not definitive |
| `insurer_discretion` | "insurer … sole discretion" | Insurer controls outcome unilaterally |
| `blanket_exclusion` | "does not cover / no coverage / not covered" | Broad exclusion without specifics |
| `conditional_payout` | "subject to / depending on / contingent on" | Payout conditions are insurer-controlled |
| `vague_conditions` | "certain situations / internal evaluation" | No objective trigger defined |
| `unilateral_change` | "reserves the right to modify / amend" | Insurer can change terms without consent |

---

## State-Specific Rules

Regulatory overlays run on every clause after the ML model. High-severity state flags can escalate the risk label by one tier (Low → Medium, Medium → High) and route the submission to Legal Review instead of direct UW Review.

### California
- **CA_PRIOR_APPROVAL** (HIGH) — Prop 103: unilateral modification language violates prior-approval requirement
- **CA_FAIR_CLAIMS** (MEDIUM) — Fair Claims Settlement Practices: claims involving loss must include a settlement timeframe
- **CA_PLAIN_LANGUAGE** (MEDIUM) — Insurance Code §756: high legalese density flags

### New York
- **NY_EXCLUSION_DISCLOSURE** (HIGH) — DFS 11 NYCRR 216: exclusions must be explicitly stated, not vague
- **NY_PROMPT_PAY** (MEDIUM) — Insurance Law §3224-a: undisputed claims paid within 30 days
- **NY_DISCRIMINATION** (MEDIUM) — Executive Law §296: protected class classifications

### Florida
- **FL_AOB** (HIGH) — SB 2-D (2022): assignment-of-benefits clauses require consumer disclosures
- **FL_HURRICANE** (MEDIUM) — Statute §627.701: hurricane deductible must be stated separately
- **FL_INFLATION** (MEDIUM) — Citizens Insurance: replacement cost needs inflation guard

### Texas
- **TX_PROMPT_PAY** (MEDIUM) — Insurance Code §542: sole-discretion language conflicts with 15-day acknowledgment requirement
- **TX_PLAIN_MEANING** (MEDIUM) — Ambiguous "may/might" coverage language construed against insurer
- **TX_CANCELLATION** (MEDIUM) — Insurance Code §551: cancellation requires grounds + notice period

### All states (NAIC)
- **NAIC_GRACE_PERIOD** — Termination clauses need a grace period provision
- **NAIC_CONCEALMENT** — Concealment voids must be proportionate to materiality

---

## Gemini 2.5 Flash Rewriter

Risky clauses (Medium and High) are rewritten using Gemini 2.5 Flash. The rewriter is context-aware: it receives the 2 clauses before and 2 after the target clause so it does not create contradictions within the policy.

**The prompt instructs Gemini to:**
1. Use "will" / "shall" instead of "may" / "might"
2. Specify exact, measurable conditions for coverage
3. Remove insurer-sole-discretion language
4. Include specific timeframes (e.g. "within 30 calendar days")
5. Comply with the submission's state regulations
6. Not contradict neighboring clauses
7. Write at a Grade 8 reading level (plain English)

**Example:**

| | Text |
|---|---|
| **Original** | Coverage may apply in certain situations subject to evaluation by the insurer, depending on internal review and surrounding circumstances. |
| **Rewritten** | The insurer shall provide coverage for all losses that meet the following objective criteria: (1) the loss event occurred within the policy period, (2) the policyholder submits a written claim with supporting documentation within 30 calendar days, and (3) the loss is not excluded under Section 4 of this policy. |

---

## Workflow State Machine

Every policy submission flows through a defined state machine. No state can be skipped.

```
SUBMITTED
    │
    ▼  (AI scores all clauses + applies state rules)
AI_ASSESSED
    │
    ├──── has HIGH-severity state flags? ──── YES ──→ LEGAL_REVIEW
    │                                                      │
    │                                              Legal team reviews
    │                                              and clears flags
    │                                                      │
    └──── no state flags ──────────────────────────────────┘
                                                           │
                                                           ▼
                                                       UW_REVIEW
                                                  (underwriter sees
                                                   clean policy +
                                                   clause diff)
                                                           │
                                            ┌──────────────┴──────────────┐
                                            ▼                             ▼
                                        APPROVED                      REJECTED
                                   (policy issued)              (returned for revision)
                                                                         │
                                                                         ▼
                                                                     SUBMITTED
                                                                   (resubmit after fix)
```

**Audit events are written at every transition.** Every `SUBMITTED`, `AI_ASSESSED`, `ROUTED`, `LEGAL_APPROVED`, `APPROVED`, `REJECTED`, and `CLAUSE_DECISION` event is persisted with timestamp, actor, and full description.

---

## Frontend (4 Tabs)

Open `frontend/index.html` directly in a browser. No build step required.

### Tab 1 — Submit Policy
- Select policy type, state, coverage amount, applicant age, prior claims, deductible
- Drag-drop a PDF or paste clauses (one per line)
- Click **⚡ Submit for AI Assessment**
- Sees: workflow state chip, risk summary (Low/Medium/High counts), state flags alert, Clean Policy view, Clause Diff view
- If routed to UW_REVIEW: Approve / Request Changes buttons
- If routed to LEGAL_REVIEW: purple banner explaining the routing

### Tab 2 — Review Queue
- **Underwriter Queue**: policies in `UW_REVIEW` state
- **Legal Queue**: policies in `LEGAL_REVIEW` state
- Badge counts on the nav tab update automatically
- Click **Review →** to open a modal with full clause diff, state flags, and Approve/Reject buttons

### Tab 3 — Audit Log
- Every event across all submissions in reverse-chronological order
- Color-coded event type chips (SUBMITTED, AI_ASSESSED, ROUTED, APPROVED, REJECTED, etc.)
- Click a submission ID to open its review modal

### Tab 4 — Dev Tools
- **Single Clause**: paste one clause, run `/assess-risk`, see raw ML output including state flags and Gemini rewrite
- **Bulk CSV**: upload a CSV with a `clause` column, batch-assess all rows, download annotated results

---

## Guidewire PolicyCenter Integration

The system is designed to plug directly into Guidewire PolicyCenter. The file `integration/guidewire_hook_simulation.py` simulates the GOSU plugin that would run inside PolicyCenter.

**In a real PolicyCenter deployment, the equivalent GOSU code would be:**

```gosu
// gw/plugin/RiskAssessmentPlugin.gs
var client = new gw.api.webservice.HttpClient()
var payload = {
  "clause": clause.Text,
  "policy_type": policyPeriod.Policy.ProductCode,
  "coverage_amount": policyPeriod.TotalCoverageAmount.Amount,
  "state": policyPeriod.BaseState.Code
}
var response = client.post("http://risk-api:8000/assess-risk", payload.toJsonString())
var result   = gw.lang.reflect.IType.parseJson(response)

if (result["risk_label"] == "High") {
  policyPeriod.addNote("AI Risk Flag: " + result["flags"].join(", "))
  var issue = new UWIssue()
  issue.ShortDescription = "AI: High-risk clause detected"
  issue.LongDescription  = result["rewritten_clause"]
  policyPeriod.UWIssues.add(issue)
}
```

**Custom fields that would be added in PolicyCenter Studio:**

| Field | Type | Purpose |
|---|---|---|
| `PolicyPeriod.ai_risk_label` | typekey RiskLevel | Low / Medium / High |
| `PolicyPeriod.ai_risk_score` | Float | Model confidence |
| `PolicyPeriod.ai_risk_flags` | String | JSON array of flag IDs |
| `PolicyPeriod.ai_assessed_at` | DateTime | Timestamp of assessment |
| `PolicyPeriod.ai_rewritten_clause` | LargeString | Gemini output |

**Where in the PolicyCenter workflow it hooks:**
- **Pre-Submission**: `OnEnter()` — runs AI, displays risk badge in PCF widget
- **Underwriting Review**: `OnApprove()` — if High clauses exist, creates UWIssue and blocks auto-approval
- **Policy Issuance**: `OnExit()` — logs final risk score to audit trail

---

## Evaluation Metrics

Run `python model/evaluate.py` after training:

```
              precision    recall  f1-score   support

        High       1.00      1.00      1.00       497
         Low       0.97      0.99      0.98      2090
      Medium       0.95      0.88      0.91       498

    accuracy                           0.97      3085
   macro avg       0.97      0.96      0.96      3085
weighted avg       0.97      0.97      0.97      3085
```

Also generates:
- `confusion_matrix.png` — heatmap of predictions vs. actuals
- `calibration_curves.png` — reliability diagram (model confidence vs. actual frequency)

**Key business metric — High recall = 1.00.** Missing a risky clause is more costly than over-flagging a safe one. The model catches every high-risk clause in the test set.

---

## Setup and Running

### Requirements
- Python 3.10+
- Gemini API key (free at https://aistudio.google.com/app/apikey)

### Install dependencies
```bash
pip install -r api/requirements.txt
```

### Set Gemini API key
Create a `.env` file in the project root:
```
GEMINI_API_KEY=your_key_here
```

### Train the model (if artifacts don't exist)
```bash
python model/train.py --data augmented_dataset.csv
```

### Start the API
```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### Open the frontend
Open `frontend/index.html` in a browser. The status dot should turn green.

### Run tests
```bash
pytest tests/ -v
```

### Interactive API docs
Visit **http://localhost:8000/docs**

---

## How to Demo (Hackathon Flow)

1. **Open Submit Policy tab** → click **Load Sample** → change state to **CA**
2. Click **⚡ Submit for AI Assessment** (takes ~20s — Gemini rewrites 4 clauses)
3. Show the **state flags alert** (CA Prop 103 fired on the unilateral change clause)
4. Show **Clean Policy** tab — this is what the underwriter approves, not the original
5. Show **Clause Diff** tab — strikethrough original, green rewritten version per clause
6. If routed to LEGAL_REVIEW: switch to **Review Queue → Legal Queue** → "Clear for UW Review"
7. Switch to **UW Review** in the queue → click Review → Approve
8. Switch to **Audit Log** — every step is timestamped and attributed

**The pitch in one sentence:** *The underwriter never reads the original risky policy — they only read the AI-cleaned version and click Approve.*

---

## Limitations

| Limitation | Status |
|---|---|
| Dataset is synthetic + public proxy data, not real policyholder records | Stated scope — augmentation strategy documented |
| Gemini free tier has rate limits (~15 req/min) | Demo works fine; production would use paid tier |
| State rules are pattern-based, not a formal legal compliance engine | Sufficient for risk flagging; production would integrate a regulatory API |
| English-language clauses only | Stated scope |
| No live Guidewire license | Integration layer is standards-compliant REST — directly portable to real PC |
| PDF extraction requires text-based PDFs (not scanned) | Documented limitation — scanned PDFs need OCR pre-processing |
