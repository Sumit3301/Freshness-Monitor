import os
import shutil
import sys
from datetime import datetime
from collections import defaultdict

# --- Word-based naming helpers ---

DAY_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    21: "twenty_one", 22: "twenty_two", 23: "twenty_three", 24: "twenty_four",
    25: "twenty_five", 26: "twenty_six", 27: "twenty_seven", 28: "twenty_eight",
    29: "twenty_nine", 30: "thirty", 31: "thirty_one",
}

MONTH_WORDS = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}

def date_to_label(dt: datetime) -> str:
    """Convert a datetime to DD-month(words)-YYYY format, e.g., 19-february-2026."""
    day_num = f"{dt.day:02d}"
    month_word = MONTH_WORDS.get(dt.month, str(dt.month))
    return f"{day_num}-{month_word}-{dt.year}"

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

INCOMING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incoming")
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

def organize_all():
    if not os.path.isdir(INCOMING_DIR):
        print(f"❌ Incoming directory '{INCOMING_DIR}' not found!")
        return

    files = sorted([f for f in os.listdir(INCOMING_DIR) if f.lower().endswith((".jpg", ".jpeg"))])
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
        run_start = run[0][0]
        run_end = run[-1][0]
        span_h = (run_end - run_start).total_seconds() / 3600
        
        # Name directory using DD-month(words)-YYYY format
        run_label = date_to_label(run_start)
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"run_{run_label}")
        
        print(f"\n📁 Processing Run {i+1}/{len(runs)}: {run_start.strftime('%d-%m-%Y %H:%M')} → "
              f"{run_end.strftime('%d-%m-%Y %H:%M')} ({len(run)} images, {span_h:.1f}h)")
        print(f"   Target Folder: {output_dir}")

        # Group by elapsed hour within this run
        by_hour = defaultdict(list)
        for ts, fname in run:
            elapsed_hours = int((ts - run_start).total_seconds() / 3600)
            by_hour[elapsed_hours].append((ts, fname))

        os.makedirs(output_dir, exist_ok=True)

        for hour in sorted(by_hour.keys()):
            images = by_hour[hour]
            mid_idx = len(images) // 2
            _, chosen_file = images[mid_idx]

            new_name = f"{hour}h.jpg"
            src = os.path.join(INCOMING_DIR, chosen_file)
            dst = os.path.join(output_dir, new_name)
            shutil.copy2(src, dst)

        print(f"   ✅ Organized {len(by_hour)} hourly images into {output_dir}")

    print("\n🎉 Done organizing all runs!")

if __name__ == "__main__":
    organize_all()
