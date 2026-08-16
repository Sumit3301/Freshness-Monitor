"""
Organize captured images by elapsed hours for training.

Auto-detects separate 'runs' from the incoming/ folder by finding
gaps in capture timestamps. Picks the LATEST run and copies one
representative image per elapsed hour, renaming to 0h.jpg, 1h.jpg etc.

Usage:
  python organize_captures.py
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import shutil
from datetime import datetime, timedelta
from collections import defaultdict

INCOMING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incoming")

# A gap larger than this between consecutive images = new run
RUN_GAP_HOURS = 4


def parse_timestamp(filename: str):
    """Extract datetime from filename like capture_20260420_214701.jpg"""
    stem = os.path.splitext(filename)[0]
    parts = stem.split("_")
    if len(parts) >= 3:
        try:
            return datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
        except ValueError:
            return None
    return None


def detect_runs(timestamped: list, gap_hours: float = RUN_GAP_HOURS):
    """
    Split a sorted list of (timestamp, filename) into separate runs
    based on time gaps > gap_hours between consecutive images.
    Returns a list of runs, each run being a list of (ts, fname).
    """
    if not timestamped:
        return []

    runs = []
    current_run = [timestamped[0]]

    for i in range(1, len(timestamped)):
        prev_ts = timestamped[i - 1][0]
        curr_ts = timestamped[i][0]
        gap = (curr_ts - prev_ts).total_seconds() / 3600

        if gap > gap_hours:
            runs.append(current_run)
            current_run = []

        current_run.append(timestamped[i])

    runs.append(current_run)
    return runs


def organize():
    files = sorted([f for f in os.listdir(INCOMING_DIR) if f.endswith(".jpg")])
    if not files:
        print("❌ No images found in incoming/")
        return

    # Parse timestamps from filenames
    timestamped = []
    for f in files:
        ts = parse_timestamp(f)
        if ts:
            timestamped.append((ts, f))

    timestamped.sort(key=lambda x: x[0])

    print(f"📊 Total images in incoming/: {len(timestamped)}")
    print(f"   Earliest: {timestamped[0][0].strftime('%d-%m-%Y %H:%M:%S')}  ({timestamped[0][1]})")
    print(f"   Latest:   {timestamped[-1][0].strftime('%d-%m-%Y %H:%M:%S')}  ({timestamped[-1][1]})")

    # Detect separate runs
    runs = detect_runs(timestamped, gap_hours=RUN_GAP_HOURS)

    print(f"\n🔍 Detected {len(runs)} run(s) (gap threshold: {RUN_GAP_HOURS}h):")
    for i, run in enumerate(runs):
        start = run[0][0]
        end = run[-1][0]
        span_h = (end - start).total_seconds() / 3600
        marker = " ← latest" if i == len(runs) - 1 else ""
        print(f"   Run {i+1:2d}: {start.strftime('%d-%m-%Y %H:%M')} → "
              f"{end.strftime('%d-%m-%Y %H:%M')}  "
              f"({len(run)} images, {span_h:.1f}h){marker}")

    # Use the latest run
    latest_run = runs[-1]
    run_start = latest_run[0][0]
    run_end   = latest_run[-1][0]
    span_hours = (run_end - run_start).total_seconds() / 3600

    run_label = run_start.strftime("%d_%m_%Y")
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"run_{run_label}")

    print(f"\n✅ Using latest run: {run_start.strftime('%d-%m-%Y %H:%M')} → "
          f"{run_end.strftime('%d-%m-%Y %H:%M')} ({len(latest_run)} images, {span_hours:.1f}h)")
    print(f"📁 Output directory: {OUTPUT_DIR}\n")

    # Group by elapsed hour within this run
    by_hour = defaultdict(list)
    for ts, fname in latest_run:
        elapsed_hours = int((ts - run_start).total_seconds() / 3600)
        by_hour[elapsed_hours].append((ts, fname))

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for hour in sorted(by_hour.keys()):
        images = by_hour[hour]
        mid_idx = len(images) // 2
        _, chosen_file = images[mid_idx]

        new_name = f"{hour}h.jpg"
        src = os.path.join(INCOMING_DIR, chosen_file)
        dst = os.path.join(OUTPUT_DIR, new_name)
        shutil.copy2(src, dst)

        print(f"   {hour:3d}h → {chosen_file}  ({len(images)} images in bucket)")

    print(f"\n✅ Organized {len(by_hour)} hourly images into {OUTPUT_DIR}")
    print(f"   Run this next:")
    print(f"     python prepare_data.py")
    print(f"     python train_model.py")

    return OUTPUT_DIR


if __name__ == "__main__":
    output = organize()
