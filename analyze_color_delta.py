import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Color Delta Analysis (ΔE from Baseline)
========================================
For each run directory:
  - Loads 0h.jpg as the "fresh baseline"
  - Extracts color stats from every hourly image
  - Computes ΔE (perceptual color distance in CIELAB space) from 0h
  - Plots per-run and aggregated color drift curves

This validates whether color change is a reliable, consistent signal
across different experimental runs — before we commit to using it
as the basis for freshness classification.

Usage:
  python analyze_color_delta.py
  python analyze_color_delta.py --runs run_16-may-2026 run_01-may-2026
"""

import os
import re
import argparse
import numpy as np
import cv2
import matplotlib
matplotlib.use("Agg")   # headless rendering — saves PNGs without a display
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from scipy.stats import pearsonr

import config


# ─── Helpers ─────────────────────────────────────────────────────────────────

def center_crop(image: np.ndarray, fraction: float = 0.4) -> np.ndarray:
    """Crop the central `fraction` of the image to isolate the reactive film."""
    h, w = image.shape[:2]
    cy, cx = h // 2, w // 2
    ch, cw = int(h * fraction), int(w * fraction)
    y1, y2 = cy - ch // 2, cy + ch // 2
    x1, x2 = cx - cw // 2, cx + cw // 2
    return image[y1:y2, x1:x2]


def extract_lab_mean(image_path: str) -> np.ndarray | None:
    """
    Load an image, center-crop, resize, convert to CIELAB and return
    the per-pixel mean [L*, a*, b*] as a 1-D array of length 3.
    Returns None if the image cannot be read.
    """
    img = cv2.imread(image_path)
    if img is None:
        return None
    cropped = center_crop(img, fraction=0.4)
    resized = cv2.resize(cropped, config.IMG_RESIZE)
    lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB).astype(np.float32)
    return lab.mean(axis=(0, 1))   # shape (3,)  → [L, a, b]


def extract_hsv_mean(image_path: str) -> np.ndarray | None:
    """Return mean [H, S, V] of center-cropped, resized image."""
    img = cv2.imread(image_path)
    if img is None:
        return None
    cropped = center_crop(img, fraction=0.4)
    resized = cv2.resize(cropped, config.IMG_RESIZE)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV).astype(np.float32)
    return hsv.mean(axis=(0, 1))


def delta_e(lab1: np.ndarray, lab2: np.ndarray) -> float:
    """
    Euclidean distance in CIELAB space (ΔE76).
    Perceptually uniform — a ΔE of ~1 is just noticeable to the human eye.
    """
    return float(np.linalg.norm(lab1 - lab2))


def parse_hours(filename: str) -> int | None:
    stem = os.path.splitext(filename)[0]
    m = re.match(r'^(\d+)\s*h(?:r|rs|our|ours)?$', stem, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ─── Per-run analysis ────────────────────────────────────────────────────────

def analyze_run(run_dir: str) -> dict | None:
    """
    Process one run directory.
    Returns a dict with:
        run_name   – directory basename
        hours      – sorted list of hour values
        delta_e    – ΔE from 0h for each hour
        lab_means  – absolute LAB means for each hour
        hsv_means  – absolute HSV means for each hour
    """
    run_name = os.path.basename(run_dir)
    baseline_path = os.path.join(run_dir, "0h.jpg")

    if not os.path.exists(baseline_path):
        # Try common alternate naming
        for alt in ["0hr.jpg", "0h.jpeg", "0hr.jpeg"]:
            alt_path = os.path.join(run_dir, alt)
            if os.path.exists(alt_path):
                baseline_path = alt_path
                break
        else:
            print(f"   ⚠️  No baseline (0h.jpg) in {run_name} — skipping.")
            return None

    baseline_lab = extract_lab_mean(baseline_path)
    if baseline_lab is None:
        print(f"   ⚠️  Cannot read baseline image in {run_name} — skipping.")
        return None

    print(f"\n📁 {run_name}")
    print(f"   Baseline LAB: L={baseline_lab[0]:.1f}  a={baseline_lab[1]:.1f}  b={baseline_lab[2]:.1f}")

    hours_list, de_list, lab_list, hsv_list = [], [], [], []

    for filename in sorted(os.listdir(run_dir)):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in config.IMAGE_EXTENSIONS:
            continue
        hours = parse_hours(filename)
        if hours is None:
            continue

        img_path = os.path.join(run_dir, filename)
        lab = extract_lab_mean(img_path)
        hsv = extract_hsv_mean(img_path)

        if lab is None:
            print(f"   ⚠️  Cannot read {filename}")
            continue

        de = delta_e(baseline_lab, lab)
        hours_list.append(hours)
        de_list.append(de)
        lab_list.append(lab)
        hsv_list.append(hsv)
        print(f"   {filename:12s}  ΔE={de:6.2f}  L={lab[0]:5.1f}  a={lab[1]:5.1f}  b={lab[2]:5.1f}")

    if not hours_list:
        return None

    # Sort by hour
    order = np.argsort(hours_list)
    return {
        "run_name": run_name,
        "hours": np.array(hours_list)[order].tolist(),
        "delta_e": np.array(de_list)[order].tolist(),
        "lab_means": np.array(lab_list)[order].tolist(),
        "hsv_means": np.array(hsv_list)[order].tolist(),
    }


# ─── Plotting ────────────────────────────────────────────────────────────────

def plot_delta_e_curves(results: list[dict], out_dir: str):
    """Plot ΔE vs hours for all runs on a single chart."""
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = cm.tab10(np.linspace(0, 1, len(results)))

    for res, color in zip(results, colors):
        ax.plot(res["hours"], res["delta_e"],
                marker="o", markersize=5, linewidth=1.8,
                label=res["run_name"], color=color)

    # Freshness stage bands (using current hour thresholds as reference)
    ax.axvspan(0,  3,  alpha=0.06, color="#2ecc71", label="Stage 1 (0–3h)  Very Fresh")
    ax.axvspan(3,  6,  alpha=0.06, color="#f1c40f", label="Stage 2 (3–6h)  Fresh")
    ax.axvspan(6,  14, alpha=0.06, color="#e67e22", label="Stage 3 (6–14h) Early Spoilage")
    ax.axvspan(14, ax.get_xlim()[1] if ax.get_xlim()[1] > 14 else 42,
               alpha=0.06, color="#e74c3c", label="Stage 4 (14h+)  Spoiled")

    ax.set_xlabel("Hours since capture start", fontsize=13)
    ax.set_ylabel("ΔE (perceptual color distance from 0h baseline)", fontsize=13)
    ax.set_title("Film Color Change Over Time — ΔE from Fresh Baseline", fontsize=15, fontweight="bold")
    ax.legend(fontsize=8, loc="upper left", ncol=2)
    ax.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()

    path = os.path.join(out_dir, "delta_e_curves.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"\n✅ Saved: {path}")
    return path


def plot_lab_channels(results: list[dict], out_dir: str):
    """Plot absolute L*, a*, b* channel means over time for each run."""
    channel_names = ["L* (Lightness)", "a* (Green↔Red)", "b* (Blue↔Yellow)"]
    channel_colors = ["#888888", "#e74c3c", "#3498db"]

    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=False)
    run_colors = cm.tab10(np.linspace(0, 1, len(results)))

    for ch_idx, (ax, ch_name, _) in enumerate(zip(axes, channel_names, channel_colors)):
        for res, color in zip(results, run_colors):
            ch_values = [row[ch_idx] for row in res["lab_means"]]
            ax.plot(res["hours"], ch_values,
                    marker=".", markersize=5, linewidth=1.5,
                    label=res["run_name"], color=color)
        ax.set_ylabel(ch_name, fontsize=11)
        ax.grid(True, linestyle="--", alpha=0.35)
        ax.legend(fontsize=7, loc="best", ncol=2)

    axes[-1].set_xlabel("Hours", fontsize=12)
    fig.suptitle("CIELAB Channel Means Over Time (per run)", fontsize=14, fontweight="bold")
    plt.tight_layout()

    path = os.path.join(out_dir, "lab_channels.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"✅ Saved: {path}")
    return path


def plot_hsv_channels(results: list[dict], out_dir: str):
    """Plot H, S, V channel means over time for each run."""
    channel_names = ["Hue (H)", "Saturation (S)", "Value / Brightness (V)"]

    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=False)
    run_colors = cm.tab10(np.linspace(0, 1, len(results)))

    for ch_idx, (ax, ch_name) in enumerate(zip(axes, channel_names)):
        for res, color in zip(results, run_colors):
            ch_values = [row[ch_idx] for row in res["hsv_means"]]
            ax.plot(res["hours"], ch_values,
                    marker=".", markersize=5, linewidth=1.5,
                    label=res["run_name"], color=color)
        ax.set_ylabel(ch_name, fontsize=11)
        ax.grid(True, linestyle="--", alpha=0.35)
        ax.legend(fontsize=7, loc="best", ncol=2)

    axes[-1].set_xlabel("Hours", fontsize=12)
    fig.suptitle("HSV Channel Means Over Time (per run)", fontsize=14, fontweight="bold")
    plt.tight_layout()

    path = os.path.join(out_dir, "hsv_channels.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"✅ Saved: {path}")
    return path


def plot_correlation(results: list[dict], out_dir: str):
    """
    Scatter plot: hours vs ΔE across ALL runs combined.
    Computes and displays Pearson correlation coefficient.
    """
    all_hours, all_de = [], []
    for res in results:
        all_hours.extend(res["hours"])
        all_de.extend(res["delta_e"])

    all_hours = np.array(all_hours)
    all_de = np.array(all_de)

    r, pval = pearsonr(all_hours, all_de)

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(all_hours, all_de, alpha=0.5, s=30, color="#3498db", edgecolors="white", linewidths=0.5)

    # Trend line
    z = np.polyfit(all_hours, all_de, 1)
    p = np.poly1d(z)
    xline = np.linspace(all_hours.min(), all_hours.max(), 200)
    ax.plot(xline, p(xline), "r--", linewidth=2, label=f"Trend (r={r:.3f}, p={pval:.2e})")

    ax.set_xlabel("Hours", fontsize=13)
    ax.set_ylabel("ΔE from 0h baseline", fontsize=13)
    ax.set_title(f"Hours vs ΔE — All Runs Combined\nPearson r = {r:.3f}  (p = {pval:.2e})", fontsize=14, fontweight="bold")
    ax.legend(fontsize=11)
    ax.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()

    path = os.path.join(out_dir, "hours_vs_delta_e.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"✅ Saved: {path}")
    return r, pval, path


def print_summary(results: list[dict], r: float, pval: float):
    """Print a concise statistical summary to the console."""
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"   Runs analysed    : {len(results)}")

    all_de_max = [max(res["delta_e"]) for res in results]
    all_de_at_0 = [res["delta_e"][0] for res in results if res["hours"][0] == 0]

    print(f"   Max ΔE per run   : {', '.join(f'{v:.1f}' for v in all_de_max)}")
    print(f"   ΔE at t=0 (self) : {', '.join(f'{v:.2f}' for v in all_de_at_0)}  (should be ~0)")
    print(f"\n   Pearson r (hours vs ΔE) : {r:.3f}")
    print(f"   p-value                 : {pval:.2e}")

    if abs(r) >= 0.75:
        verdict = "✅ STRONG — color change is a reliable freshness signal!"
    elif abs(r) >= 0.5:
        verdict = "⚠️  MODERATE — color changes with time but with noise."
    else:
        verdict = "❌ WEAK — color change does NOT reliably track time/freshness."

    print(f"\n   Verdict: {verdict}")
    print("=" * 60)


# ─── Main ────────────────────────────────────────────────────────────────────

def main(specific_runs: list[str] | None = None):
    out_dir = os.path.join(config.BASE_DIR, "results", "color_delta_analysis")
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 60)
    print("🔬 Color Delta Analysis — ΔE from Fresh Baseline")
    print("=" * 60)

    # Discover run directories
    if specific_runs:
        run_dirs = []
        for r in specific_runs:
            p = os.path.join(config.BASE_DIR, r)
            if os.path.isdir(p):
                run_dirs.append(p)
            else:
                print(f"⚠️  Run not found: {p}")
    else:
        run_dirs = [
            os.path.join(config.BASE_DIR, d)
            for d in sorted(os.listdir(config.BASE_DIR))
            if d.startswith("run_") and os.path.isdir(os.path.join(config.BASE_DIR, d))
        ]

    if not run_dirs:
        print("❌ No run directories found!")
        return

    print(f"\nFound {len(run_dirs)} run(s): {[os.path.basename(r) for r in run_dirs]}")

    # Analyse each run
    results = []
    for run_dir in run_dirs:
        res = analyze_run(run_dir)
        if res and len(res["hours"]) >= 3:   # skip runs with too few images
            results.append(res)

    if not results:
        print("❌ No usable runs found (each run needs ≥3 hourly images + 0h baseline).")
        return

    # Generate plots
    print("\n📈 Generating plots...")
    plot_delta_e_curves(results, out_dir)
    plot_lab_channels(results, out_dir)
    plot_hsv_channels(results, out_dir)
    r, pval, _ = plot_correlation(results, out_dir)

    # Summary
    print_summary(results, r, pval)
    print(f"\n📂 All charts saved to: {out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyse color delta (ΔE) from fresh baseline across all runs.")
    parser.add_argument("--runs", type=str, nargs="+",
                        help="Optional: specific run folder names to analyse (e.g. run_16-may-2026)")
    args = parser.parse_args()
    main(specific_runs=args.runs)
