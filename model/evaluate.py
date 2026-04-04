"""
model/evaluate.py
Loads saved artifacts and prints a full evaluation report.
Run: python model/evaluate.py --data data/augmented_dataset.csv
"""

import argparse
import os
import joblib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.sparse import hstack, csr_matrix
from sklearn.metrics import (
    classification_report, confusion_matrix,
    ConfusionMatrixDisplay, roc_auc_score
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import label_binarize
from sklearn.calibration import calibration_curve

ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
CATEGORICAL_COLS = ["policy_type", "state"]
NUMERIC_COLS     = ["coverage_amount", "applicant_age",
                    "prior_claims_count", "deductible_amount"]


def load_artifacts():
    tfidf = joblib.load(os.path.join(ARTIFACTS_DIR, "tfidf_vectorizer.pkl"))
    lr    = joblib.load(os.path.join(ARTIFACTS_DIR, "lr_model.pkl"))
    svm   = joblib.load(os.path.join(ARTIFACTS_DIR, "svm_model.pkl"))
    le    = joblib.load(os.path.join(ARTIFACTS_DIR, "label_encoder.pkl"))
    sp_path = os.path.join(ARTIFACTS_DIR, "struct_preprocessor.pkl")
    sp    = joblib.load(sp_path) if os.path.exists(sp_path) else None
    return tfidf, lr, svm, le, sp


def evaluate(data_path: str):
    df = pd.read_csv(data_path)
    tfidf, lr, svm, le, sp = load_artifacts()
    classes = list(le.classes_)

    has_struct = sp is not None and all(c in df.columns for c in CATEGORICAL_COLS + NUMERIC_COLS)
    X_text   = df["clause"]
    X_struct = df[CATEGORICAL_COLS + NUMERIC_COLS] if has_struct else None
    y        = le.transform(df["risk"])

    if has_struct:
        _, X_te, _, Xs_te, _, y_te = train_test_split(
            X_text, X_struct, y, test_size=0.20, stratify=y, random_state=42
        )
    else:
        _, X_te, _, y_te = train_test_split(
            X_text, y, test_size=0.20, stratify=y, random_state=42
        )
        Xs_te = None

    Xt_te  = tfidf.transform(X_te)
    lr_p   = lr.predict_proba(Xt_te)

    if has_struct:
        Xs_proc = sp.transform(Xs_te)
        if not hasattr(Xs_proc, "toarray"):
            Xs_proc = csr_matrix(Xs_proc)
        Xc = hstack([Xt_te, csr_matrix(lr_p), Xs_proc])
    else:
        Xc = hstack([Xt_te, csr_matrix(lr_p)])

    y_pred  = svm.predict(Xc)
    y_proba = svm.predict_proba(Xc)

    # ── Text report ───────────────────────────────────────────────────────
    print("=" * 55)
    print("CLASSIFICATION REPORT")
    print("=" * 55)
    print(classification_report(y_te, y_pred, target_names=classes))

    y_te_bin = label_binarize(y_te, classes=range(len(classes)))
    print("ROC-AUC (one-vs-rest):")
    for i, cls in enumerate(classes):
        auc = roc_auc_score(y_te_bin[:, i], y_proba[:, i])
        print(f"  {cls:8s}: {auc:.4f}")

    # ── Confusion matrix plot ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    ConfusionMatrixDisplay(confusion_matrix(y_te, y_pred),
                           display_labels=classes).plot(ax=axes[0], colorbar=False)
    axes[0].set_title("Confusion Matrix (counts)")
    ConfusionMatrixDisplay(confusion_matrix(y_te, y_pred, normalize="true").round(2),
                           display_labels=classes).plot(ax=axes[1], colorbar=False)
    axes[1].set_title("Confusion Matrix (normalised)")
    plt.tight_layout()
    plt.savefig("confusion_matrix.png", dpi=150, bbox_inches="tight")
    print("\nSaved: confusion_matrix.png")

    # ── Calibration curves ────────────────────────────────────────────────
    fig, axes = plt.subplots(1, len(classes), figsize=(5 * len(classes), 4))
    if len(classes) == 1:
        axes = [axes]
    for i, cls in enumerate(classes):
        fp, mp = calibration_curve(y_te_bin[:, i], y_proba[:, i], n_bins=10)
        axes[i].plot(mp, fp, "s-", label="Model")
        axes[i].plot([0, 1], [0, 1], "k--", label="Perfect")
        axes[i].set_title(f"Calibration — {cls}")
        axes[i].legend()
    plt.tight_layout()
    plt.savefig("calibration_curves.png", dpi=150, bbox_inches="tight")
    print("Saved: calibration_curves.png")
    plt.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/augmented_dataset.csv")
    args = parser.parse_args()
    evaluate(args.data)
