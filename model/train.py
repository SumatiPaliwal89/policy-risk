"""
model/train.py
Trains the full risk assessment pipeline and saves artifacts.
Run: python model/train.py --data data/augmented_dataset.csv
"""

import argparse
import os
import time
import joblib
import numpy as np
import pandas as pd
from scipy.sparse import hstack, csr_matrix
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, OneHotEncoder, StandardScaler
from sklearn.svm import SVC

ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")

CATEGORICAL_COLS = ["policy_type", "state"]
NUMERIC_COLS     = ["coverage_amount", "applicant_age",
                    "prior_claims_count", "deductible_amount"]


def load_data(path: str):
    df = pd.read_csv(path)
    required = {"clause", "risk"}
    assert required.issubset(df.columns), f"Missing columns: {required - set(df.columns)}"
    print(f"Loaded {len(df):,} rows  |  classes: {df['risk'].value_counts().to_dict()}")
    return df


def build_struct_preprocessor():
    return ColumnTransformer(transformers=[
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=True), CATEGORICAL_COLS),
        ("num", StandardScaler(), NUMERIC_COLS),
    ])


def train(data_path: str, test_size: float = 0.20, random_state: int = 42):
    df = load_data(data_path)

    # ── Labels ──────────────────────────────────────────────────────────
    le = LabelEncoder()
    y  = le.fit_transform(df["risk"])

    # ── Features ────────────────────────────────────────────────────────
    has_struct = all(c in df.columns for c in CATEGORICAL_COLS + NUMERIC_COLS)
    X_text    = df["clause"]
    X_struct  = df[CATEGORICAL_COLS + NUMERIC_COLS] if has_struct else None

    # ── Split ────────────────────────────────────────────────────────────
    if has_struct:
        X_tt, X_te, Xs_tt, Xs_te, y_tt, y_te = train_test_split(
            X_text, X_struct, y, test_size=test_size,
            stratify=y, random_state=random_state
        )
    else:
        X_tt, X_te, y_tt, y_te = train_test_split(
            X_text, y, test_size=test_size,
            stratify=y, random_state=random_state
        )
        Xs_tt = Xs_te = None

    print(f"Train: {len(y_tt):,}  |  Test: {len(y_te):,}")

    # ── TF-IDF ───────────────────────────────────────────────────────────
    tfidf = TfidfVectorizer(max_features=20000, ngram_range=(1, 3), sublinear_tf=True)
    Xt_tt = tfidf.fit_transform(X_tt)
    Xt_te = tfidf.transform(X_te)

    # ── Stage 1: LR on text ───────────────────────────────────────────────
    lr = LogisticRegression(max_iter=3000, solver="lbfgs", C=1.0, multi_class="multinomial")
    lr.fit(Xt_tt, y_tt)
    lr_p_tt = lr.predict_proba(Xt_tt)
    lr_p_te = lr.predict_proba(Xt_te)
    print(f"LR text accuracy: {lr.score(Xt_te, y_te):.4f}")

    # ── Stage 2: SVM on combined features ────────────────────────────────
    if has_struct:
        sp = build_struct_preprocessor()
        Xs_tt_proc = sp.fit_transform(Xs_tt)
        Xs_te_proc = sp.transform(Xs_te)
        if not hasattr(Xs_tt_proc, "toarray"):
            Xs_tt_proc = csr_matrix(Xs_tt_proc)
            Xs_te_proc = csr_matrix(Xs_te_proc)
        Xc_tt = hstack([Xt_tt, csr_matrix(lr_p_tt), Xs_tt_proc])
        Xc_te = hstack([Xt_te, csr_matrix(lr_p_te), Xs_te_proc])
    else:
        sp = None
        Xc_tt = hstack([Xt_tt, csr_matrix(lr_p_tt)])
        Xc_te = hstack([Xt_te, csr_matrix(lr_p_te)])

    svm_base = SVC(kernel="rbf", C=10, gamma="scale", probability=True, random_state=42)
    svm = CalibratedClassifierCV(svm_base, cv=3, method="sigmoid")

    t0 = time.perf_counter()
    svm.fit(Xc_tt, y_tt)
    print(f"SVM trained in {time.perf_counter()-t0:.1f}s")
    print(f"SVM accuracy:  {svm.score(Xc_te, y_te):.4f}")

    # ── Save ─────────────────────────────────────────────────────────────
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    joblib.dump(tfidf, os.path.join(ARTIFACTS_DIR, "tfidf_vectorizer.pkl"))
    joblib.dump(lr,    os.path.join(ARTIFACTS_DIR, "lr_model.pkl"))
    joblib.dump(svm,   os.path.join(ARTIFACTS_DIR, "svm_model.pkl"))
    joblib.dump(le,    os.path.join(ARTIFACTS_DIR, "label_encoder.pkl"))
    if sp:
        joblib.dump(sp, os.path.join(ARTIFACTS_DIR, "struct_preprocessor.pkl"))

    print(f"\nArtifacts saved to {ARTIFACTS_DIR}/")
    return tfidf, lr, svm, le, sp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/augmented_dataset.csv")
    parser.add_argument("--test-size", type=float, default=0.20)
    args = parser.parse_args()
    train(args.data, test_size=args.test_size)
