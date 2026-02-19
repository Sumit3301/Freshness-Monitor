"""
Central configuration for the Freshness Classification System.
Generic, item-agnostic — works with any reactive film images.
Update these values to match your environment.
"""
import os
import re
import glob

# ─── Paths ───────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
INCOMING_DIR = os.path.join(BASE_DIR, "incoming")       # SCP drops images here
RESULTS_DIR = os.path.join(BASE_DIR, "results")          # Classification results
MODEL_PATH = os.path.join(MODEL_DIR, "classifier.pkl")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.pkl")

# ─── Training Data Discovery ────────────────────────────────────────
# List of directories containing training images.
# Each directory should have images named by time — e.g. 0h.jpg, 4hr.jpg, 12h.jpeg
# Set to None to auto-discover (any subdir with image files).
TRAINING_DIRS = None  # auto-discover

# Directories / patterns to skip during auto-discovery
_SKIP_DIRS = {"augmented", "model", "incoming", "results", "barcodes",
              "temp", "__pycache__", ".git", ".venv", "venv"}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}


def discover_training_dirs():
    """Auto-discover subdirectories that contain image files."""
    if TRAINING_DIRS is not None:
        return TRAINING_DIRS

    dirs = []
    for entry in os.scandir(BASE_DIR):
        if not entry.is_dir():
            continue
        if entry.name.lower() in _SKIP_DIRS:
            continue
        # Check if dir (or any child) contains image files
        has_images = False
        for root, _, files in os.walk(entry.path):
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    has_images = True
                    break
            if has_images:
                break
        if has_images:
            dirs.append(entry.path)
    return dirs


def parse_hours_from_filename(filename: str):
    """
    Extract hour value from a filename like '0h.jpg', '4hr.jpg', '12hr.jpeg', '2h.jpeg'.
    Returns the hour as an integer, or None if not parseable.
    """
    stem = os.path.splitext(filename)[0]
    m = re.match(r'^(\d+)\s*h(?:r|rs|our|ours)?$', stem, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ─── Stage Mapping ──────────────────────────────────────────────────
# Map hour value → freshness stage (0-3)
def hour_to_stage(hours: int) -> int:
    """Map elapsed hours to a freshness stage."""
    if hours <= 3:
        return 0   # Stage 1: Very Fresh
    elif hours <= 6:
        return 1   # Stage 2: Fresh
    elif hours <= 14:
        return 2   # Stage 3: Early Spoilage
    else:
        return 3   # Stage 4: Spoiled


LABEL_NAMES = {
    0: "Stage 1 - Very Fresh",
    1: "Stage 2 - Fresh",
    2: "Stage 3 - Early Spoilage",
    3: "Stage 4 - Spoiled",
}

# Stage color codes (for barcode/dashboard visualization)
STAGE_COLORS = {
    0: "#2ecc71",   # green
    1: "#f1c40f",   # yellow
    2: "#e67e22",   # orange
    3: "#e74c3c",   # red
}

BARCODE_DIR = os.path.join(BASE_DIR, "barcodes")

# ─── Server Settings ────────────────────────────────────────────────
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000
WATCHER_POLL_INTERVAL = 2   # seconds between folder scans

# ─── Raspberry Pi / SCP Settings ────────────────────────────────────
PI_CAPTURE_DIR = "/home/pi/captures"           # where Pi stores captured images
PI_CAMERA_INTERVAL = 10                        # seconds between captures
LOCAL_SERVER_IP = "192.168.1.100"              # ← CHANGE to your PC's LAN IP
LOCAL_SERVER_USER = "Acer"                     # ← CHANGE to your Windows username
LOCAL_SCP_DEST = INCOMING_DIR                  # destination folder on local PC
LOCAL_SERVER_URL = f"http://{LOCAL_SERVER_IP}:{SERVER_PORT}"

# ─── Feature Extraction Settings ────────────────────────────────────
IMG_RESIZE = (256, 256)          # resize images before feature extraction
HSV_BINS = (8, 8, 8)            # histogram bins for H, S, V channels
RGB_BINS = (8, 8, 8)            # histogram bins for R, G, B channels
N_DOMINANT_COLORS = 3           # k-means clusters for dominant color extraction
