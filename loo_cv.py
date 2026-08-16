import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Leave-One-Out Cross-Validation (LOO-CV)
========================================
For each image in the run:
  - Trains on all OTHER images (+ their augments)
  - Tests on the held-out image (no augments — raw original only)

This is the most honest performance estimate for a 19-image dataset.
Every image is tested exactly once on a model that never saw it.

Usage:
  python loo_cv.py --run run_20-april-2026
"""

import os
import argparse
import numpy as np
import cv2
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix

import config
from prepare_data import extract_features, augment_image


def discover_images(run_dir: str):
    """Return sorted list of (filepath, label, filename) for a run directory."""
    images = []
    for filename in sorted(os.listdir(run_dir)):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in config.IMAGE_EXTENSIONS:
            continue
        hours = config.parse_hours_from_filename(filename)
        if hours is None:
            continue
        label = config.hour_to_stage(hours)
        images.append((os.path.join(run_dir, filename), label, filename))
    return images


def extract_numeric(feats: dict) -> dict:
    """Strip string features (hex colors), keep only numeric."""
    return {k: v for k, v in feats.items() if not isinstance(v, str)}


def build_feature_matrix(image_entries, augments_per_image: int = 5, skip_idx: int = -1):
    """
    Extract features from image_entries, optionally augmenting each.
    Skips the entry at skip_idx (the held-out test image).
    Returns X (np.ndarray), y (np.ndarray), feature_keys (list).
    """
    X_rows, y_rows = [], []
    feature_keys = None

    for i, (filepath, label, filename) in enumerate(image_entries):
        if i == skip_idx:
            continue

        # Original image
        try:
            feats = extract_numeric(extract_features(filepath))
        except Exception as e:
            print(f"   ⚠️  Skipping {filename}: {e}")
            continue

        if feature_keys is None:
            feature_keys = sorted(feats.keys())

        X_rows.append([feats.get(k, 0.0) for k in feature_keys])
        y_rows.append(label)

        # Augmented versions (training only)
        img = cv2.imread(filepath)
        if img is not None:
            img_resized = cv2.resize(img, config.IMG_RESIZE)
            aug_dir = os.path.join(config.BASE_DIR, "augmented")
            os.makedirs(aug_dir, exist_ok=True)

            for j, aug_img in enumerate(augment_image(img_resized, n_augments=augments_per_image)):
                aug_name = f"loo_{os.path.splitext(filename)[0]}_aug{j}.jpg"
                aug_path = os.path.join(aug_dir, aug_name)
                cv2.imwrite(aug_path, aug_img)
                try:
                    aug_feats = extract_numeric(extract_features(aug_path))
                    X_rows.append([aug_feats.get(k, 0.0) for k in feature_keys])
                    y_rows.append(label)
                except Exception:
                    pass

    return np.array(X_rows), np.array(y_rows), feature_keys


def run_loo_cv(run_name: str, n_augments: int = 5):
    run_dir = os.path.join(config.BASE_DIR, run_name)
    if not os.path.isdir(run_dir):
        print(f"❌ Run folder not found: {run_dir}")
        return

    images = discover_images(run_dir)
    if len(images) < 4:
        print("❌ Need at least 4 images for LOO-CV.")
        return

    print("=" * 65)
    print(f"🔁 Leave-One-Out CV  —  {run_name}")
    print(f"   {len(images)} images, {n_augments} augments per training image")
    print("=" * 65)

    y_true, y_pred_rf, y_pred_svm = [], [], []
    filenames = []

    for test_idx, (test_path, test_label, test_filename) in enumerate(images):
        # ── Build training set (all except test_idx) ──────────────
        X_train, y_train, feat_keys = build_feature_matrix(
            images, augments_per_image=n_augments, skip_idx=test_idx
        )

        if len(X_train) == 0 or feat_keys is None:
            print(f"   ⚠️  Could not build training set for fold {test_idx}")
            continue

        # ── Extract test features ──────────────────────────────────
        try:
            test_feats = extract_numeric(extract_features(test_path))
            X_test = np.array([[test_feats.get(k, 0.0) for k in feat_keys]])
        except Exception as e:
            print(f"   ⚠️  Cannot extract features from {test_filename}: {e}")
            continue

        # ── Scale ──────────────────────────────────────────────────
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s  = scaler.transform(X_test)

        # ── Train RF ───────────────────────────────────────────────
        rf = RandomForestClassifier(
            n_estimators=100, max_depth=5,
            random_state=42, class_weight="balanced"
        )
        rf.fit(X_train_s, y_train)
        pred_rf = rf.predict(X_test_s)[0]
        conf_rf = max(rf.predict_proba(X_test_s)[0])

        # ── Train SVM ──────────────────────────────────────────────
        svm = SVC(kernel="rbf", probability=True,
                  class_weight="balanced", random_state=42)
        svm.fit(X_train_s, y_train)
        pred_svm = svm.predict(X_test_s)[0]

        # ── Record ─────────────────────────────────────────────────
        y_true.append(test_label)
        y_pred_rf.append(pred_rf)
        y_pred_svm.append(pred_svm)
        filenames.append(test_filename)

        mark = "✅" if pred_rf == test_label else "❌"
        exp  = config.LABEL_NAMES[test_label]
        got  = config.LABEL_NAMES[pred_rf]
        print(f"   {mark} [{test_idx+1:02d}/{len(images)}] {test_filename:10s}  "
              f"expected={exp:25s}  RF={got:25s}  conf={conf_rf:.0%}")

    # ── Summary ────────────────────────────────────────────────────
    if not y_true:
        print("❌ No results to report.")
        return

    y_true  = np.array(y_true)
    y_pred_rf  = np.array(y_pred_rf)
    y_pred_svm = np.array(y_pred_svm)

    rf_acc  = np.mean(y_true == y_pred_rf)  * 100
    svm_acc = np.mean(y_true == y_pred_svm) * 100

    print("\n" + "=" * 65)
    print("📊 LOO-CV RESULTS")
    print("=" * 65)
    print(f"   RandomForest LOO-CV Accuracy : {rf_acc:.1f}%")
    print(f"   SVM          LOO-CV Accuracy : {svm_acc:.1f}%")

    print(f"\n   {'─'*40}")
    print("   Per-stage accuracy (RandomForest):")
    for stage_id, name in config.LABEL_NAMES.items():
        mask = y_true == stage_id
        if mask.sum() == 0:
            continue
        stage_acc = np.mean(y_pred_rf[mask] == stage_id) * 100
        bar = "█" * int(stage_acc / 10) + "░" * (10 - int(stage_acc / 10))
        print(f"   {name:30s}  {bar}  {stage_acc:.0f}%  ({int(mask.sum())} images)")

    print(f"\n   {'─'*40}")
    print("   Classification Report (RandomForest):")
    unique = sorted(set(y_true) | set(y_pred_rf))
    print(classification_report(
        y_true, y_pred_rf,
        labels=unique,
        target_names=[config.LABEL_NAMES[i] for i in unique],
        zero_division=0
    ))

    print("=" * 65)
    if rf_acc >= 80:
        verdict = "✅ Strong — model generalizes well within this run."
    elif rf_acc >= 60:
        verdict = "⚠️  Moderate — reasonable for a single-run dataset."
    else:
        verdict = "❌ Weak — model struggles even within the same run."
    print(f"   Verdict: {verdict}")
    print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Leave-One-Out CV on a single run.")
    parser.add_argument("--run", type=str, default="run_20-april-2026",
                        help="Run folder to evaluate (default: run_20-april-2026)")
    parser.add_argument("--augments", type=int, default=5,
                        help="Augments per training image (default: 5)")
    args = parser.parse_args()
    run_loo_cv(args.run, n_augments=args.augments)
