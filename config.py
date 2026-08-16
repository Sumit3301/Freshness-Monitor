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

# ─── Load local environment configuration (.env) ──────────────────────
dotenv_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(dotenv_path):
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("=", 1)
            if len(parts) == 2:
                key, val = parts[0].strip(), parts[1].strip()
                if val.startswith(('"', "'")) and val.endswith(('"', "'")):
                    val = val[1:-1]
                os.environ[key] = val

MODEL_DIR = os.path.join(BASE_DIR, "model")
INCOMING_DIR = os.path.join(BASE_DIR, "incoming")       # SCP drops images here
RESULTS_DIR = os.path.join(BASE_DIR, "results")          # Classification results
MODEL_PATH = os.path.join(MODEL_DIR, "classifier.pkl")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.pkl")

# ─── Training Data Discovery ────────────────────────────────────────
# Toggle between training on all run_ directories or only the latest one.
TRAIN_ON_ALL_RUNS = True  # Set to False to only train on the latest run

def _find_latest_run():
    """Return the most recently dated run_ directory."""
    from datetime import datetime
    run_dirs = []
    for d in os.listdir(BASE_DIR):
        if d.startswith("run_") and os.path.isdir(os.path.join(BASE_DIR, d)):
            timestamp_str = d[4:]
            dt = None
            for fmt in ["%d_%m_%Y_%H%M", "%d_%m_%Y", "%Y%m%d_%H%M", "%Y%m%d"]:
                try:
                    dt = datetime.strptime(timestamp_str, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                try:
                    dt = datetime.fromtimestamp(os.path.getmtime(os.path.join(BASE_DIR, d)))
                except Exception:
                    dt = datetime.min
            run_dirs.append((dt, os.path.join(BASE_DIR, d)))
            
    if not run_dirs:
        return None
    run_dirs.sort(key=lambda x: x[0])
    return [run_dirs[-1][1]]

def _get_training_dirs():
    if TRAIN_ON_ALL_RUNS:
        return None  # Will auto-discover all directories containing images
    else:
        return _find_latest_run()

TRAINING_DIRS = _get_training_dirs()

# Directories / patterns to skip during auto-discovery
_SKIP_DIRS = {"augmented", "model", "incoming", "results", "barcodes",
              "temp", "__pycache__", ".git", ".venv", "venv", "backup"}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}


def discover_training_dirs():
    """Auto-discover subdirectories that contain image files."""
    if TRAINING_DIRS is not None:
        return TRAINING_DIRS

    dirs = []
    
    # Check for subdirectories inside 'Final Runs' directory
    final_runs_dir = os.path.join(BASE_DIR, "Final Runs")
    if os.path.isdir(final_runs_dir):
        for entry in os.scandir(final_runs_dir):
            if entry.is_dir():
                dirs.append(entry.path)

    for entry in os.scandir(BASE_DIR):
        if not entry.is_dir():
            continue
        if entry.name.lower() in _SKIP_DIRS or entry.name == "Final Runs":
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
# Map hour value → freshness stage (0-2)
def hour_to_stage(hours: int) -> int:
    """Map elapsed hours to 3 freshness stages."""
    if hours <= 6:
        return 0   # Stage 1: Fresh (0-6h)
    elif hours <= 14:
        return 1   # Stage 2: Spoiling (7-14h)
    else:
        return 2   # Stage 3: Spoiled (>14h)


LABEL_NAMES = {
    0: "Stage 1 - Fresh",
    1: "Stage 2 - Spoiling",
    2: "Stage 3 - Spoiled",
}

# Stage color codes (for barcode/dashboard visualization)
STAGE_COLORS = {
    0: "#2ecc71",   # green
    1: "#f1c40f",   # yellow/orange
    2: "#e74c3c",   # red
}

BARCODE_DIR = os.path.join(BASE_DIR, "barcodes")
DB_PATH = os.path.join(BASE_DIR, "freshness.db")

# ─── Server Settings ────────────────────────────────────────────────
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000
WATCHER_POLL_INTERVAL = 2   # seconds between folder scans

# ─── Raspberry Pi / SCP Settings ────────────────────────────────────
PI_CAPTURE_DIR = "/home/pi/captures"           # where Pi stores captured images
PI_CAMERA_INTERVAL = 10                        # seconds between captures
LOCAL_SERVER_IP = "100.126.82.18"              # ← CHANGE to your PC's LAN IP
LOCAL_SERVER_USER = "Acer"                     # ← CHANGE to your Windows username
LOCAL_SCP_DEST = INCOMING_DIR                  # destination folder on local PC
LOCAL_SERVER_URL = f"http://{LOCAL_SERVER_IP}:{SERVER_PORT}"

# ─── Feature Extraction Settings ────────────────────────────────────
IMG_RESIZE = (256, 256)          # resize images before feature extraction
HSV_BINS = (8, 8, 8)            # histogram bins for H, S, V channels
RGB_BINS = (8, 8, 8)            # histogram bins for R, G, B channels
N_DOMINANT_COLORS = 3           # k-means clusters for dominant color extraction

# ─── Email Alert Settings ───────────────────────────────────────────
# Alerts are sent once per stage transition (e.g., Fresh → Early Spoilage).
# Repeated predictions of the same stage do NOT trigger additional emails.
ALERT_ENABLED = os.environ.get("ALERT_ENABLED", "true").lower() == "true"
ALERT_SMTP_HOST = os.environ.get("ALERT_SMTP_HOST", "smtp.gmail.com")
ALERT_SMTP_PORT = int(os.environ.get("ALERT_SMTP_PORT", "587"))
ALERT_SMTP_USE_TLS = os.environ.get("ALERT_SMTP_USE_TLS", "true").lower() == "true"
ALERT_SMTP_USE_SSL = os.environ.get("ALERT_SMTP_USE_SSL", "false").lower() == "true"
ALERT_EMAIL_SENDER = os.environ.get("ALERT_EMAIL_SENDER", "")
ALERT_EMAIL_PASSWORD = os.environ.get("ALERT_EMAIL_PASSWORD", "")
ALERT_EMAIL_RECIPIENTS = [
    e.strip() for e in os.environ.get("ALERT_EMAIL_RECIPIENTS", "").split(",") if e.strip()
]
# All stages trigger alerts (once per transition)
ALERT_STAGES = {0, 1, 2}

# ─── Raspberry Pi Heartbeat Monitoring ──────────────────────────────
# The server tracks the last time the Pi sent any data (frame, image, etc.).
# If no heartbeat is received within PI_HEARTBEAT_TIMEOUT seconds,
# an "offline" email alert is sent. A "back online" email follows recovery.
PI_HEARTBEAT_TIMEOUT = int(os.environ.get("PI_HEARTBEAT_TIMEOUT", "60"))     # seconds
PI_HEARTBEAT_CHECK_INTERVAL = int(os.environ.get("PI_HEARTBEAT_CHECK", "15"))  # seconds
PI_OFFLINE_EMAIL_COOLDOWN = int(os.environ.get("PI_OFFLINE_COOLDOWN", "300"))  # seconds
