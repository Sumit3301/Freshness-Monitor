#!/usr/bin/env python3
"""
Fish Freshness Data Preparation Pipeline
========================================
Extracts color features specifically for fish freshness classification
from the 5 fish runs in Final Runs/ directory.

Usage:
    python prepare_fish_data.py
"""

import os
import sys
import csv
import numpy as np
import cv2
from sklearn.cluster import KMeans

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import config

FISH_RUNS_DIR = os.path.join(config.BASE_DIR, "Final Runs")

FISH_RUN_FOLDERS = [
    "run_04-july-2026_fish5",
    "run_20-april-2026_fish6",
    "run_26-june-2026_fish",
    "run_28-june-2026_fish_3",
    "run_29-june-2026_fish4",
]


def compute_color_histogram(image: np.ndarray, color_space: str = "hsv") -> np.ndarray:
    """Compute a normalized color histogram."""
    if color_space == "hsv":
        converted = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        bins = config.HSV_BINS
    else:
        converted = image.copy()
        bins = config.RGB_BINS

    hist = cv2.calcHist(
        [converted], [0, 1, 2], None,
        list(bins),
        [0, 180, 0, 256, 0, 256] if color_space == "hsv" else [0, 256, 0, 256, 0, 256]
    )
    hist = cv2.normalize(hist, hist).flatten()
    return hist


def compute_color_stats(image: np.ndarray) -> dict:
    """Compute rich perceptual features: HSV, RGB, CIE L*a*b*, and ratio indices."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    rgb = image.astype(np.float32)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)

    stats = {}
    # HSV Stats
    for i, ch_name in enumerate(["h", "s", "v"]):
        stats[f"{ch_name}_mean"] = float(np.mean(hsv[:, :, i]))
        stats[f"{ch_name}_std"] = float(np.std(hsv[:, :, i]))
        stats[f"{ch_name}_median"] = float(np.median(hsv[:, :, i]))

    # RGB Stats
    r_ch = rgb[:, :, 2]
    g_ch = rgb[:, :, 1]
    b_ch = rgb[:, :, 0]
    for i, (ch_name, ch_data) in enumerate(zip(["b", "g", "r"], [b_ch, g_ch, r_ch])):
        stats[f"{ch_name}_mean"] = float(np.mean(ch_data))
        stats[f"{ch_name}_std"] = float(np.std(ch_data))
        stats[f"{ch_name}_median"] = float(np.median(ch_data))

    # CIE L*a*b* Stats (Perceptually Uniform - Excellent for chemical film color shifts)
    for i, ch_name in enumerate(["lab_L", "lab_a", "lab_b"]):
        stats[f"{ch_name}_mean"] = float(np.mean(lab[:, :, i]))
        stats[f"{ch_name}_std"] = float(np.std(lab[:, :, i]))
        stats[f"{ch_name}_median"] = float(np.median(lab[:, :, i]))

    # Color Ratios & Index Features
    r_mean = stats["r_mean"]
    g_mean = stats["g_mean"]
    b_mean = stats["b_mean"]
    stats["ratio_rb"] = float(r_mean / (b_mean + 1e-5))
    stats["ratio_gb"] = float(g_mean / (b_mean + 1e-5))
    stats["ratio_rg"] = float(r_mean / (g_mean + 1e-5))
    stats["norm_diff_rb"] = float((r_mean - b_mean) / (r_mean + b_mean + 1e-5))

    return stats


def compute_dominant_colors(image: np.ndarray, n_colors: int = 3):
    """Extract dominant colors using k-means clustering."""
    pixels = image.reshape(-1, 3).astype(np.float32)
    sums = pixels.sum(axis=1)
    mask = (sums > 30) & (sums < 720)
    filtered_pixels = pixels[mask]

    if len(filtered_pixels) < 100:
        filtered_pixels = pixels

    if len(filtered_pixels) > 5000:
        indices = np.random.choice(len(filtered_pixels), 5000, replace=False)
        filtered_pixels = filtered_pixels[indices]

    kmeans = KMeans(n_clusters=n_colors, n_init=10, random_state=42)
    kmeans.fit(filtered_pixels)

    colors = kmeans.cluster_centers_.astype(int)
    hex_colors = []
    for bgr in colors:
        hex_color = "#{:02x}{:02x}{:02x}".format(bgr[2], bgr[1], bgr[0])
        hex_colors.append(hex_color)

    return hex_colors, colors


def extract_features(image_path: str) -> dict:
    """Extract color features from a single fish film image."""
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")

    h, w = image.shape[:2]
    cy, cx = h // 2, w // 2
    crop_h, crop_w = int(h * 0.4), int(w * 0.4)
    y1, y2 = cy - crop_h // 2, cy + crop_h // 2
    x1, x2 = cx - crop_w // 2, cx + crop_w // 2
    cropped = image[y1:y2, x1:x2]

    image_resized = cv2.resize(cropped, config.IMG_RESIZE)

    features = {}

    # 1D Channel Histograms (16 bins per channel for compact representation)
    hsv = cv2.cvtColor(image_resized, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image_resized, cv2.COLOR_BGR2LAB)
    
    for ch, ch_name, max_val in [(0, "h", 180), (1, "s", 256), (2, "v", 256)]:
        h_1d = cv2.calcHist([hsv], [ch], None, [16], [0, max_val]).flatten()
        h_1d = h_1d / (h_1d.sum() + 1e-5)
        for i, val in enumerate(h_1d):
            features[f"hist_1d_{ch_name}_{i}"] = float(val)

    for ch, ch_name in [(1, "lab_a"), (2, "lab_b")]:
        h_1d = cv2.calcHist([lab], [ch], None, [16], [0, 256]).flatten()
        h_1d = h_1d / (h_1d.sum() + 1e-5)
        for i, val in enumerate(h_1d):
            features[f"hist_1d_{ch_name}_{i}"] = float(val)

    # 3D Histograms
    hsv_hist = compute_color_histogram(image_resized, "hsv")
    rgb_hist = compute_color_histogram(image_resized, "rgb")

    for i, val in enumerate(hsv_hist):
        features[f"hsv_hist_{i}"] = float(val)
    for i, val in enumerate(rgb_hist):
        features[f"rgb_hist_{i}"] = float(val)

    stats = compute_color_stats(image_resized)
    features.update(stats)

    hex_colors, bgr_colors = compute_dominant_colors(image_resized, config.N_DOMINANT_COLORS)
    for i, (hex_c, bgr_c) in enumerate(zip(hex_colors, bgr_colors)):
        features[f"dominant_{i}_hex"] = hex_c
        features[f"dominant_{i}_r"] = int(bgr_c[2])
        features[f"dominant_{i}_g"] = int(bgr_c[1])
        features[f"dominant_{i}_b"] = int(bgr_c[0])

    return features


def augment_image(image: np.ndarray, n_augments: int = 5) -> list:
    """Generate augmented versions of an image."""
    augmented = []
    h, w = image.shape[:2]

    for _ in range(n_augments):
        img = image.copy()

        brightness = np.random.uniform(0.7, 1.3)
        img = cv2.convertScaleAbs(img, alpha=brightness, beta=0)

        contrast = np.random.uniform(0.8, 1.2)
        img = cv2.convertScaleAbs(img, alpha=contrast, beta=np.random.randint(-20, 20))

        angle = np.random.uniform(-15, 15)
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)

        if np.random.random() > 0.5:
            img = cv2.flip(img, 1)

        noise = np.random.normal(0, 5, img.shape).astype(np.uint8)
        img = cv2.add(img, noise)

        augmented.append(img)

    return augmented


def hour_to_stage_3class(hours: int) -> int:
    """Map elapsed hours to 3 freshness stages."""
    if hours <= 6:
        return 0  # Stage 1: Fresh (0-6h)
    elif hours <= 14:
        return 1  # Stage 2: Spoiling (7-14h)
    else:
        return 2  # Stage 3: Spoiled (>14h)


LABEL_NAMES_3CLASS = {
    0: "Stage 1 - Fresh",
    1: "Stage 2 - Spoiling",
    2: "Stage 3 - Spoiled",
}


def prepare_fish_dataset():
    """Discover fish training images, extract features, and save to fish_features_3stage.csv."""
    print("=" * 60)
    print("🐟 Fish Freshness Classification — Data Preparation (3-Stage)")
    print("=" * 60)

    aug_dir = os.path.join(config.BASE_DIR, "augmented_fish")
    os.makedirs(aug_dir, exist_ok=True)
    os.makedirs(config.MODEL_DIR, exist_ok=True)

    fish_images = []
    for run_folder in FISH_RUN_FOLDERS:
        folder_path = os.path.join(FISH_RUNS_DIR, run_folder)
        if not os.path.isdir(folder_path):
            print(f"⚠️ Warning: Run directory not found: {folder_path}")
            continue

        for filename in sorted(os.listdir(folder_path)):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in config.IMAGE_EXTENSIONS:
                continue

            hours = config.parse_hours_from_filename(filename)
            if hours is None:
                continue

            label = hour_to_stage_3class(hours)
            filepath = os.path.join(folder_path, filename)
            source = f"Final Runs/{run_folder}/{filename}"
            fish_images.append((filepath, label, source))

    print(f"\n🖼️ Found {len(fish_images)} fish training images across {len(FISH_RUN_FOLDERS)} runs.")

    all_features = []
    for filepath, label, source in fish_images:
        label_name = LABEL_NAMES_3CLASS[label]
        print(f"📷 Processing: {source} → {label_name}")

        try:
            features = extract_features(filepath)
        except Exception as e:
            print(f"   ⚠️ Error processing {filepath}: {e}")
            continue

        numeric_features = {k: v for k, v in features.items() if not isinstance(v, str)}
        numeric_features["label"] = label
        numeric_features["source"] = source
        all_features.append(numeric_features)

        # Augmentation
        image = cv2.imread(filepath)
        image_resized = cv2.resize(image, config.IMG_RESIZE)
        aug_images = augment_image(image_resized, n_augments=5)

        for i, aug_img in enumerate(aug_images):
            aug_name = f"{os.path.splitext(os.path.basename(filepath))[0]}_aug{i}.jpg"
            aug_path = os.path.join(aug_dir, aug_name)
            cv2.imwrite(aug_path, aug_img)

            aug_feats = extract_features(aug_path)
            aug_num = {k: v for k, v in aug_feats.items() if not isinstance(v, str)}
            aug_num["label"] = label
            aug_num["source"] = f"augmented_fish/{aug_name}"
            all_features.append(aug_num)

    csv_path = os.path.join(config.BASE_DIR, "fish_features_3stage.csv")
    if all_features:
        all_keys = sorted(list(all_features[0].keys()))
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys)
            writer.writeheader()
            for row in all_features:
                writer.writerow(row)

        print(f"\n✅ Saved {len(all_features)} fish samples to {csv_path}")
        print(f"   Features per sample: {len(all_keys) - 2} (excluding label and source)")
        return csv_path
    else:
        print("❌ No features extracted!")
        return None


if __name__ == "__main__":
    prepare_fish_dataset()
