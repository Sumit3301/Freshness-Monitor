import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Model Generalization Test
=========================
Tests the saved model against images from a DIFFERENT run than it was trained on.
This is the real measure of how well the model generalizes.

Usage:
  python test_model.py --run run_16-may-2026
  python test_model.py --run run_16-may-2026 run_03-may-2026
"""

import os
import argparse
import numpy as np
import joblib

import config
from prepare_data import extract_features


def test_on_run(run_dirs: list[str]):
    # Load model & scaler
    if not os.path.exists(config.MODEL_PATH):
        print("❌ No trained model found! Run train_model.py first.")
        return
    model  = joblib.load(config.MODEL_PATH)
    scaler = joblib.load(config.SCALER_PATH)

    print("=" * 65)
    print("🧪 Model Generalization Test — Unseen Run(s)")
    print("=" * 65)

    all_correct = 0
    all_total   = 0
    per_stage   = {i: {"correct": 0, "total": 0} for i in config.LABEL_NAMES}

    for run_dir in run_dirs:
        run_path = os.path.join(config.BASE_DIR, run_dir)
        if not os.path.isdir(run_path):
            print(f"❌ Run folder not found: {run_path}")
            continue

        print(f"\n📁 Testing on: {run_dir}")
        print("-" * 65)

        results = []
        for filename in sorted(os.listdir(run_path)):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in config.IMAGE_EXTENSIONS:
                continue
            hours = config.parse_hours_from_filename(filename)
            if hours is None:
                continue

            expected_stage = config.hour_to_stage(hours)
            image_path     = os.path.join(run_path, filename)

            try:
                feats = extract_features(image_path)
                # Keep only numeric features, sorted same as training
                numeric = {k: v for k, v in feats.items() if not isinstance(v, str)}
                feature_vec = np.array([numeric[k] for k in sorted(numeric.keys())]).reshape(1, -1)
                feature_scaled = scaler.transform(feature_vec)

                pred  = model.predict(feature_scaled)[0]
                proba = model.predict_proba(feature_scaled)[0]
                conf  = max(proba)
            except Exception as e:
                print(f"   ⚠️  {filename}: error — {e}")
                continue

            correct = (pred == expected_stage)
            mark    = "✅" if correct else "❌"
            expected_name = config.LABEL_NAMES[expected_stage]
            pred_name     = config.LABEL_NAMES[pred]
            print(f"   {mark} {filename:10s}  expected={expected_name:25s}  pred={pred_name:25s}  conf={conf:.0%}")

            results.append(correct)
            per_stage[expected_stage]["total"]   += 1
            per_stage[expected_stage]["correct"] += int(correct)
            all_total   += 1
            all_correct += int(correct)

        run_acc = sum(results) / len(results) * 100 if results else 0
        print(f"\n   Run accuracy: {run_acc:.1f}%  ({sum(results)}/{len(results)} correct)")

    # ── Overall summary ──────────────────────────────────────────
    print("\n" + "=" * 65)
    print("📊 OVERALL GENERALIZATION SUMMARY")
    print("=" * 65)
    overall_acc = all_correct / all_total * 100 if all_total else 0
    print(f"   Total accuracy : {overall_acc:.1f}%  ({all_correct}/{all_total})\n")

    print("   Per-stage breakdown:")
    for stage_id, name in config.LABEL_NAMES.items():
        s = per_stage[stage_id]
        if s["total"] == 0:
            print(f"   {name:30s}  —  no samples")
        else:
            acc = s["correct"] / s["total"] * 100
            bar = "█" * int(acc / 10) + "░" * (10 - int(acc / 10))
            print(f"   {name:30s}  {bar}  {acc:.0f}%  ({s['correct']}/{s['total']})")

    print("=" * 65)
    if overall_acc >= 80:
        print("✅ Good generalization — model transfers well to unseen data!")
    elif overall_acc >= 60:
        print("⚠️  Moderate generalization — usable but needs more training data.")
    else:
        print("❌ Poor generalization — model is overfit to the training run.")
    print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test model generalization on unseen run(s).")
    parser.add_argument("--run", nargs="+", required=True,
                        help="Run folder name(s) to test on (e.g. run_16-may-2026)")
    args = parser.parse_args()
    test_on_run(args.run)
