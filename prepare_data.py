import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Data Preparation Pipeline
=========================
Extracts color features from reactive film images for freshness classification.

Features extracted (whole-image only — item-agnostic):
  - HSV histogram (flattened)
  - RGB histogram (flattened)
  - Mean & std of H, S, V channels
  - Mean & std of R, G, B channels
  - Dominant colors via k-means

Usage:
  python prepare_data.py
  → outputs features.csv + augmented images in augmented/
"""

import os
import csv
import numpy as np
import cv2
from sklearn.cluster import KMeans

import config


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
    """Compute mean and std of each channel in HSV and RGB."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    rgb = image.astype(np.float32)

    stats = {}
    for i, ch_name in enumerate(["h", "s", "v"]):
        stats[f"{ch_name}_mean"] = np.mean(hsv[:, :, i])
        stats[f"{ch_name}_std"] = np.std(hsv[:, :, i])
    for i, ch_name in enumerate(["r", "g", "b"]):
        stats[f"{ch_name}_mean"] = np.mean(rgb[:, :, 2 - i])  # BGR → RGB
        stats[f"{ch_name}_std"] = np.std(rgb[:, :, 2 - i])

    return stats


def compute_dominant_colors(image: np.ndarray, n_colors: int = 3) -> list:
    """Extract dominant colors using k-means clustering."""
    pixels = image.reshape(-1, 3).astype(np.float32)

    # Filter out very dark (shadows) and very bright (glare) pixels
    # This prevents the AI from thinking a black shadow is a "dominant color"
    sums = pixels.sum(axis=1)
    mask = (sums > 30) & (sums < 720)
    filtered_pixels = pixels[mask]
    
    # Fallback just in case the entire image was dark
    if len(filtered_pixels) < 100:
        filtered_pixels = pixels

    # Subsample for speed
    if len(filtered_pixels) > 5000:
        indices = np.random.choice(len(filtered_pixels), 5000, replace=False)
        filtered_pixels = filtered_pixels[indices]

    kmeans = KMeans(n_clusters=n_colors, n_init=10, random_state=42)
    kmeans.fit(filtered_pixels)

    colors = kmeans.cluster_centers_.astype(int)
    # Convert BGR to hex
    hex_colors = []
    for bgr in colors:
        hex_color = "#{:02x}{:02x}{:02x}".format(bgr[2], bgr[1], bgr[0])
        hex_colors.append(hex_color)

    return hex_colors, colors


def extract_features(image_path: str) -> dict:
    """
    Extract color features from a single film image.
    Returns a dictionary of feature name → value.
    """
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")

    # Crop the central 40% of the image to isolate the reactive film
    # and strictly ignore background, table, or plate colors.
    h, w = image.shape[:2]
    cy, cx = h // 2, w // 2
    crop_h, crop_w = int(h * 0.4), int(w * 0.4)
    y1, y2 = cy - crop_h // 2, cy + crop_h // 2
    x1, x2 = cx - crop_w // 2, cx + crop_w // 2
    cropped = image[y1:y2, x1:x2]

    # Resize for consistency
    image_resized = cv2.resize(cropped, config.IMG_RESIZE)

    features = {}

    # ─── Full image color histograms ────────────────────────────
    hsv_hist = compute_color_histogram(image_resized, "hsv")
    rgb_hist = compute_color_histogram(image_resized, "rgb")

    for i, val in enumerate(hsv_hist):
        features[f"hsv_hist_{i}"] = float(val)
    for i, val in enumerate(rgb_hist):
        features[f"rgb_hist_{i}"] = float(val)

    # ─── Channel statistics ─────────────────────────────────────
    stats = compute_color_stats(image_resized)
    features.update(stats)

    # ─── Dominant colors ────────────────────────────────────────
    hex_colors, bgr_colors = compute_dominant_colors(image_resized, config.N_DOMINANT_COLORS)
    for i, (hex_c, bgr_c) in enumerate(zip(hex_colors, bgr_colors)):
        features[f"dominant_{i}_hex"] = hex_c
        features[f"dominant_{i}_r"] = int(bgr_c[2])
        features[f"dominant_{i}_g"] = int(bgr_c[1])
        features[f"dominant_{i}_b"] = int(bgr_c[0])

    return features


def augment_image(image: np.ndarray, n_augments: int = 5) -> list:
    """
    Generate augmented versions of an image for training data expansion.
    Uses brightness, contrast, rotation, flip, and slight color shifts.
    """
    augmented = []
    h, w = image.shape[:2]

    for i in range(n_augments):
        img = image.copy()

        # Random brightness shift
        brightness = np.random.uniform(0.7, 1.3)
        img = cv2.convertScaleAbs(img, alpha=brightness, beta=0)

        # Random contrast shift
        contrast = np.random.uniform(0.8, 1.2)
        img = cv2.convertScaleAbs(img, alpha=contrast, beta=np.random.randint(-20, 20))

        # Random rotation (-15 to +15 degrees)
        angle = np.random.uniform(-15, 15)
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)

        # Random horizontal flip
        if np.random.random() > 0.5:
            img = cv2.flip(img, 1)

        # Random Gaussian noise
        noise = np.random.normal(0, 5, img.shape).astype(np.uint8)
        img = cv2.add(img, noise)

        augmented.append(img)

    return augmented


def discover_training_images(specific_runs=None):
    """
    Discover training images from all configured training directories.
    Parses filenames to extract hour values and map to freshness stages.

    Returns list of (filepath, label, source_name) tuples.
    """
    if specific_runs:
        training_dirs = []
        for r in specific_runs:
            p = os.path.join(config.BASE_DIR, r)
            if os.path.isdir(p):
                training_dirs.append(p)
            else:
                print(f"❌ Specific run folder not found: {p}")
    else:
        training_dirs = config.discover_training_dirs()
        
    images = []

    if not training_dirs:
        print("❌ No training directories found!")
        return images

    print(f"\n📁 Discovered {len(training_dirs)} training directory(ies):")
    for d in training_dirs:
        print(f"   → {d}")

    for dir_path in training_dirs:
        dir_name = os.path.basename(dir_path)
        # Walk directory tree to find images
        for root, _, files in os.walk(dir_path):
            for filename in sorted(files):
                ext = os.path.splitext(filename)[1].lower()
                if ext not in config.IMAGE_EXTENSIONS:
                    continue

                hours = config.parse_hours_from_filename(filename)
                if hours is None:
                    print(f"   ⚠️  Skipping (can't parse hours): {filename}")
                    continue

                label = config.hour_to_stage(hours)
                filepath = os.path.join(root, filename)
                source = f"{dir_name}/{filename}"
                images.append((filepath, label, source))

    return images


def prepare_dataset(specific_runs=None):
    """
    Main function: discover training images, extract features, save to CSV.
    Also creates augmented versions for expanding the training set.
    """
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    aug_dir = os.path.join(config.BASE_DIR, "augmented")
    os.makedirs(aug_dir, exist_ok=True)

    all_features = []
    feature_names = None

    print("=" * 60)
    print("📊 Freshness Classification — Data Preparation")
    print("=" * 60)

    training_images = discover_training_images(specific_runs=specific_runs)
    if not training_images:
        print("❌ No training images found!")
        return None

    print(f"\n🖼️  Found {len(training_images)} training images\n")

    for filepath, label, source in training_images:
        label_name = config.LABEL_NAMES[label]
        print(f"📷 Processing: {source} → {label_name}")

        try:
            # Original image features
            features = extract_features(filepath)
        except Exception as e:
            print(f"   ⚠️  Error: {e}")
            continue

        # Remove hex string features for the numeric CSV
        numeric_features = {k: v for k, v in features.items() if not isinstance(v, str)}
        numeric_features["label"] = label
        numeric_features["source"] = source
        all_features.append(numeric_features)

        if feature_names is None:
            feature_names = list(numeric_features.keys())

        # Print key hex values
        hex_keys = {k: v for k, v in features.items() if isinstance(v, str)}
        if hex_keys:
            print(f"   🎨 Color hex values: {hex_keys}")

        # Augmentation
        image = cv2.imread(filepath)
        image_resized = cv2.resize(image, config.IMG_RESIZE)
        augmented_images = augment_image(image_resized, n_augments=5)

        for i, aug_img in enumerate(augmented_images):
            aug_filename = f"{os.path.splitext(os.path.basename(filepath))[0]}_aug{i}.jpg"
            aug_path = os.path.join(aug_dir, aug_filename)
            cv2.imwrite(aug_path, aug_img)

            aug_features = extract_features(aug_path)
            aug_numeric = {k: v for k, v in aug_features.items() if not isinstance(v, str)}
            aug_numeric["label"] = label
            aug_numeric["source"] = aug_filename
            all_features.append(aug_numeric)

        print(f"   ✅ Original + {len(augmented_images)} augmented = {1 + len(augmented_images)} samples")

    # Save to CSV
    csv_path = os.path.join(config.BASE_DIR, "features.csv")
    if all_features:
        # Ensure all feature dicts have the same keys
        all_keys = set()
        for f in all_features:
            all_keys.update(f.keys())
        all_keys = sorted(all_keys)

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys)
            writer.writeheader()
            for row in all_features:
                writer.writerow(row)

        print(f"\n✅ Saved {len(all_features)} samples to {csv_path}")
        print(f"   Features per sample: {len(all_keys) - 2} (excluding label and source)")
    else:
        print("❌ No features extracted!")

    return csv_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Prepare freshness dataset features.")
    parser.add_argument("--runs", type=str, help="Comma-separated list of run directories to process")
    args = parser.parse_args()
    
    specific_runs = None
    if args.runs:
        specific_runs = [r.strip() for r in args.runs.split(",") if r.strip()]
        
    prepare_dataset(specific_runs=specific_runs)
