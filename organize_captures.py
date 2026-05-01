"""
Organize captured images by elapsed hours for training.
Takes timestamp-named captures (capture_YYYYMMDD_HHMMSS.jpg) and
copies one representative image per hour into a training directory,
renaming them to the format expected by prepare_data.py (e.g., 0h.jpg, 1h.jpg).
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import shutil
from datetime import datetime
from collections import defaultdict

INCOMING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incoming")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_20260420")


def parse_timestamp(filename: str) -> datetime:
    """Extract datetime from filename like capture_20260420_214701.jpg"""
    stem = os.path.splitext(filename)[0]  # capture_20260420_214701
    parts = stem.split("_")  # ['capture', '20260420', '214701']
    if len(parts) >= 3:
        return datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
    return None


def organize():
    files = sorted([f for f in os.listdir(INCOMING_DIR) if f.endswith(".jpg")])
    if not files:
        print("❌ No images found in incoming/")
        return

    # Parse all timestamps
    timestamped = []
    for f in files:
        ts = parse_timestamp(f)
        if ts:
            timestamped.append((ts, f))

    timestamped.sort(key=lambda x: x[0])
    start_time = timestamped[0][0]

    print(f"📊 Found {len(timestamped)} images")
    print(f"   Start: {start_time}")
    print(f"   End:   {timestamped[-1][0]}")
    total_hours = (timestamped[-1][0] - start_time).total_seconds() / 3600
    print(f"   Span:  {total_hours:.1f} hours")

    # Group by elapsed hour
    by_hour = defaultdict(list)
    for ts, fname in timestamped:
        elapsed_hours = int((ts - start_time).total_seconds() / 3600)
        by_hour[elapsed_hours].append((ts, fname))

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"\n📁 Output directory: {OUTPUT_DIR}")
    print(f"   Picking middle image from each hour bucket:\n")

    for hour in sorted(by_hour.keys()):
        images = by_hour[hour]
        # Pick the middle image (most representative of that hour)
        mid_idx = len(images) // 2
        _, chosen_file = images[mid_idx]

        new_name = f"{hour}h.jpg"
        src = os.path.join(INCOMING_DIR, chosen_file)
        dst = os.path.join(OUTPUT_DIR, new_name)
        shutil.copy2(src, dst)

        print(f"   {hour:2d}h → {chosen_file} ({len(images)} images in bucket)")

    print(f"\n✅ Organized {len(by_hour)} hourly images into {OUTPUT_DIR}")
    print(f"   Ready for: python prepare_data.py && python train_model.py")


if __name__ == "__main__":
    organize()
