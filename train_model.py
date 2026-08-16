import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Model Training Script
=====================
Trains a multi-stage freshness classifier (4 stages) using extracted film color features.

Uses:
  - RandomForest as primary classifier
  - SVM as secondary option
  - Leave-One-Out Cross-Validation (ideal for small datasets)

Usage:
  python train_model.py
"""

import os
import csv
import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import classification_report, accuracy_score

import config


def load_features(csv_path: str):
    """Load features from CSV, returning X (features) and y (labels)."""
    features = []
    labels = []
    sources = []

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_label = int(row.pop("label"))
            label = min(raw_label, 2)  # Clamp to 3 stages (0: Fresh, 1: Spoiling, 2: Spoiled)
            source = row.pop("source", "")
            # Convert remaining numeric features
            feat = []
            for k in sorted(row.keys()):
                try:
                    feat.append(float(row[k]))
                except (ValueError, TypeError):
                    feat.append(0.0)
            features.append(feat)
            labels.append(label)
            sources.append(source)

    return np.array(features), np.array(labels), sources


def train_and_evaluate(specific_runs=None):
    """Train the model with cross-validation and save it."""
    csv_path = os.path.join(config.BASE_DIR, "features.csv")

    if not os.path.exists(csv_path):
        print("❌ features.csv not found! Run prepare_data.py first.")
        return

    print("=" * 60)
    print("📊 Freshness Classification — Model Training")
    print("=" * 60)

    X, y, sources = load_features(csv_path)

    if specific_runs:
        print(f"⌛ Filtering dataset for runs: {specific_runs}")
        filtered_X = []
        filtered_y = []
        filtered_sources = []
        for i in range(0, len(X), 6):
            chunk_X = X[i:i+6]
            chunk_y = y[i:i+6]
            chunk_src = sources[i:i+6]
            if len(chunk_src) > 0:
                orig_src = chunk_src[0]
                match = False
                for r in specific_runs:
                    if r in orig_src:
                        match = True
                        break
                if match:
                    filtered_X.extend(chunk_X)
                    filtered_y.extend(chunk_y)
                    filtered_sources.extend(chunk_src)
        X = np.array(filtered_X)
        y = np.array(filtered_y)
        sources = filtered_sources
        if len(X) == 0:
            print("❌ No samples matched the specified runs!")
            return

    print(f"\n📊 Dataset: {len(X)} samples, {X.shape[1]} features")
    for stage_id, stage_name in config.LABEL_NAMES.items():
        print(f"   {stage_name}: {np.sum(y == stage_id)} samples")

    # ─── Standardize features ───────────────────────────────────
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # ─── Classifiers ────────────────────────────────────────────
    from sklearn.ensemble import ExtraTreesClassifier, VotingClassifier
    rf = RandomForestClassifier(n_estimators=200, max_depth=8, random_state=42, class_weight="balanced")
    et = ExtraTreesClassifier(n_estimators=200, max_depth=8, random_state=42, class_weight="balanced")
    svm = SVC(kernel="rbf", C=10.0, probability=True, class_weight="balanced", random_state=42)
    voting_ensemble = VotingClassifier(
        estimators=[("rf", rf), ("et", et), ("svm", svm)],
        voting="soft"
    )

    models = {
        "RandomForest": rf,
        "ExtraTrees": et,
        "SVM": svm,
        "Voting Ensemble": voting_ensemble,
    }

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    best_model = None
    best_name = ""
    best_score = -1.0

    print("\n🔍 Evaluating Classifiers with 5-Fold Cross-Validation:")
    for name, clf in models.items():
        scores = cross_val_score(clf, X_scaled, y, cv=cv, scoring="accuracy")
        mean_acc = scores.mean()
        std_acc = scores.std()
        print(f"   {name:18s}: {mean_acc:.2%} (±{std_acc:.2%})")
        if mean_acc > best_score:
            best_score = mean_acc
            best_name = name
            best_model = clf

    print(f"\n🏆 Best model selected: {best_name} ({best_score:.2%})")

    # ─── Final training on full dataset ─────────────────────────
    best_model.fit(X_scaled, y)

    # Full dataset evaluation
    y_pred = best_model.predict(X_scaled)
    print("\n📋 Full dataset classification report:")
    unique_labels = sorted(set(y) | set(y_pred))
    print(classification_report(
        y, y_pred,
        labels=unique_labels,
        target_names=[config.LABEL_NAMES[i] for i in unique_labels],
    ))

    # ─── Save model and scaler ──────────────────────────────────
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    joblib.dump(best_model, config.MODEL_PATH)
    joblib.dump(scaler, config.SCALER_PATH)

    print(f"✅ Model saved: {config.MODEL_PATH}")
    print(f"✅ Scaler saved: {config.SCALER_PATH}")

    # ─── Per-image prediction breakdown ─────────────────────────
    print("\n📋 Per-image predictions:")
    for i, source in enumerate(sources):
        pred = best_model.predict(X_scaled[i:i+1])[0]
        prob = best_model.predict_proba(X_scaled[i:i+1])[0]
        actual = config.LABEL_NAMES[y[i]]
        predicted = config.LABEL_NAMES[pred]
        match = "✅" if pred == y[i] else "❌"
        print(f"   {match} {source:25s} actual={actual:30s} pred={predicted:30s} conf={max(prob):.2%}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train freshness classification model.")
    parser.add_argument("--runs", type=str, help="Comma-separated list of run folders to train on")
    args = parser.parse_args()
    
    specific_runs = None
    if args.runs:
        specific_runs = [r.strip() for r in args.runs.split(",") if r.strip()]
        
    train_and_evaluate(specific_runs=specific_runs)
