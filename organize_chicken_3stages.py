#!/usr/bin/env python3
"""
Organize Chicken Images into 3 Stage Folders
============================================
Copies all chicken images from Final Runs/ into 3 distinct folders:
  - chicken_stages/Stage_1_Fresh  (0 - 6 hours)
  - chicken_stages/Stage_2_Spoiling (7 - 14 hours)
  - chicken_stages/Stage_3_Spoiled (> 14 hours)

Usage:
    python organize_chicken_3stages.py
"""

import os
import shutil
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import config

FINAL_RUNS_DIR = os.path.join(config.BASE_DIR, "Final Runs")

CHICKEN_RUN_FOLDERS = [
    "run_16-may-2026_chicken1",
    "run_26-july-2026_chicken2",
    "run_30-july-2026_Chicken3",
    "run_02-august-2026_chicken4",
]

OUTPUT_BASE_DIR = os.path.join(config.BASE_DIR, "chicken_stages")

STAGE_FOLDERS = {
    0: os.path.join(OUTPUT_BASE_DIR, "Stage_1_Fresh"),
    1: os.path.join(OUTPUT_BASE_DIR, "Stage_2_Spoiling"),
    2: os.path.join(OUTPUT_BASE_DIR, "Stage_3_Spoiled"),
}


def hour_to_stage_3class(hours: int) -> int:
    """Map elapsed hours to 3 freshness stages."""
    if hours <= 6:
        return 0  # Stage 1: Fresh (0-6h)
    elif hours <= 14:
        return 1  # Stage 2: Spoiling (7-14h)
    else:
        return 2  # Stage 3: Spoiled (>14h)


def organize_chicken_into_3stage_folders():
    print("=" * 60)
    print("🐔 Organizing Chicken Images into 3 Stage Folders")
    print("=" * 60)

    for folder_path in STAGE_FOLDERS.values():
        os.makedirs(folder_path, exist_ok=True)

    counts = {0: 0, 1: 0, 2: 0}

    for run_folder in CHICKEN_RUN_FOLDERS:
        run_path = os.path.join(FINAL_RUNS_DIR, run_folder)
        if not os.path.isdir(run_path):
            print(f"⚠️ Warning: Folder not found: {run_path}")
            continue

        for filename in sorted(os.listdir(run_path)):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in config.IMAGE_EXTENSIONS:
                continue

            hours = config.parse_hours_from_filename(filename)
            if hours is None:
                continue

            stage = hour_to_stage_3class(hours)
            src_file = os.path.join(run_path, filename)
            
            dst_filename = f"{run_folder}_{filename}"
            dst_file = os.path.join(STAGE_FOLDERS[stage], dst_filename)

            shutil.copy2(src_file, dst_file)
            counts[stage] += 1

    print("\n📁 Summary of Chicken Images Saved to 3 Stage Folders:")
    print(f"   - Stage 1 (Fresh)     : {counts[0]} images -> {STAGE_FOLDERS[0]}")
    print(f"   - Stage 2 (Spoiling)  : {counts[1]} images -> {STAGE_FOLDERS[1]}")
    print(f"   - Stage 3 (Spoiled)   : {counts[2]} images -> {STAGE_FOLDERS[2]}")
    print("\n🎉 Done organizing chicken images into 3 stage folders!")


if __name__ == "__main__":
    organize_chicken_into_3stage_folders()
