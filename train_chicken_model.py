#!/usr/bin/env python3
"""
Chicken Freshness Model Training Pipeline (3-Stage)
===================================================
Trains a 3-stage freshness classification model specifically for chicken
using extracted color features from chicken_features_3stage.csv.

Outputs:
  - model/chicken_classifier.pkl
  - model/chicken_scaler.pkl
  - model/chicken_feature_names.pkl
  - model/classifier.pkl (Active Model)
  - model/scaler.pkl (Active Scaler)

Usage:
    python train_chicken_model.py
"""

import os
import sys
import csv
import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, GradientBoostingClassifier, VotingClassifier
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix
from sklearn.feature_selection import SelectPercentile, f_classif

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import config
from prepare_chicken_data import LABEL_NAMES_3CLASS


def load_chicken_features(csv_path: str):
    """Load chicken features from CSV, returning X, y, sources, and feature_names."""
    features = []
    labels = []
    sources = []

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        feature_names = sorted([h for h in headers if h not in ("label", "source")])

        for row in reader:
            labels.append(int(row["label"]))
            sources.append(row.get("source", ""))
            feat = [float(row[k]) if row[k] != "" else 0.0 for k in feature_names]
            features.append(feat)

    return np.array(features), np.array(labels), sources, feature_names


def train_chicken_model():
    csv_path = os.path.join(config.BASE_DIR, "chicken_features_3stage.csv")

    if not os.path.exists(csv_path):
        print("❌ chicken_features_3stage.csv not found! Running prepare_chicken_data.py first...")
        from prepare_chicken_data import prepare_chicken_dataset
        csv_path = prepare_chicken_dataset()
        if not csv_path or not os.path.exists(csv_path):
            print("❌ Data preparation failed.")
            return

    print("=" * 60)
    print("🐔 Chicken Freshness Classification — Model Training (3-Stage)")
    print("=" * 60)

    X, y, sources, feature_names = load_chicken_features(csv_path)

    print(f"\n📊 Dataset Loaded: {len(X)} samples across {X.shape[1]} features")
    for stage_id, stage_name in LABEL_NAMES_3CLASS.items():
        count = np.sum(y == stage_id)
        print(f"   {stage_name}: {count} samples ({count / len(y):.1%})")

    # Feature Selection: Select top 15% most discriminative features
    selector = SelectPercentile(f_classif, percentile=15)
    X_selected = selector.fit_transform(X, y)
    print(f"   Reduced features from {X.shape[1]} to {X_selected.shape[1]} using ANOVA F-score selection.")

    # Standard Scaling
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_selected)

    rf = RandomForestClassifier(n_estimators=200, max_depth=15, class_weight="balanced", random_state=42)
    et = ExtraTreesClassifier(n_estimators=200, max_depth=15, class_weight="balanced", random_state=42)
    gb = GradientBoostingClassifier(n_estimators=150, learning_rate=0.08, max_depth=5, random_state=42)
    svm = SVC(kernel="rbf", C=15.0, gamma="scale", class_weight="balanced", probability=True, random_state=42)

    voting_ensemble = VotingClassifier(
        estimators=[("rf", rf), ("et", et), ("gb", gb), ("svm", svm)],
        voting="soft"
    )

    models = {
        "RandomForest": rf,
        "ExtraTrees": et,
        "GradientBoosting": gb,
        "SVM (RBF)": svm,
        "Voting Ensemble 🏆": voting_ensemble
    }

    print("\n🔍 Evaluating Classifiers with 5-Fold Stratified Cross-Validation:")
    best_name = None
    best_score = -1.0
    best_clf = None

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    for name, clf in models.items():
        scores = cross_val_score(clf, X_scaled, y, cv=skf, scoring="accuracy")
        mean_acc = np.mean(scores)
        std_acc = np.std(scores)
        print(f"   {name:20s}: {mean_acc:.2%} (±{std_acc:.2%})")

        if mean_acc > best_score:
            best_score = mean_acc
            best_name = name
            best_clf = clf

    print(f"\n🏆 Best Classifier Selected: {best_name} (3-Stage Cross-Validation Accuracy: {best_score:.2%})")

    # Fit final model on full chicken dataset
    best_clf.fit(X_scaled, y)
    y_pred = best_clf.predict(X_scaled)

    print("\n📈 Final Model Training Performance Summary (Chicken 3-Stage):")
    target_names = [LABEL_NAMES_3CLASS[i] for i in sorted(np.unique(y))]
    report = classification_report(y, y_pred, target_names=target_names)
    print(report)

    cm = confusion_matrix(y, y_pred)
    print("🧩 Confusion Matrix:")
    print(cm)

    # Save Chicken Model artifacts
    model_dir = config.MODEL_DIR
    os.makedirs(model_dir, exist_ok=True)

    chicken_model_path = os.path.join(model_dir, "chicken_classifier.pkl")
    chicken_scaler_path = os.path.join(model_dir, "chicken_scaler.pkl")
    chicken_feats_path = os.path.join(model_dir, "chicken_feature_names.pkl")

    joblib.dump(best_clf, chicken_model_path)
    joblib.dump(scaler, chicken_scaler_path)
    joblib.dump(feature_names, chicken_feats_path)

    # Update active model & scaler
    main_model_path = os.path.join(model_dir, "classifier.pkl")
    main_scaler_path = os.path.join(model_dir, "scaler.pkl")
    joblib.dump(best_clf, main_model_path)
    joblib.dump(scaler, main_scaler_path)

    print("\n💾 Model Artifacts Successfully Saved:")
    print(f"   - Chicken Model  : {chicken_model_path}")
    print(f"   - Chicken Scaler : {chicken_scaler_path}")
    print(f"   - Main Model     : {main_model_path}")
    print(f"   - Main Scaler    : {main_scaler_path}")
    print("\n🎉 Chicken model training complete!")


if __name__ == "__main__":
    train_chicken_model()
