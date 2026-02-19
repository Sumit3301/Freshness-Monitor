#!/usr/bin/env python3
"""
Raspberry Pi Image Capture & Transfer Script
=============================================
This script runs on the Raspberry Pi and:
  1. Captures images from the Pi Camera at regular intervals
  2. Transfers them to the local server via SCP or HTTP POST
  3. Logs transfer status and server responses

Requirements on the Pi:
  pip3 install picamera2 paramiko requests

Setup:
  1. Update the configuration below with your server's IP address
  2. Set up SSH key auth:  ssh-keygen && ssh-copy-id user@server_ip
  3. Run:  python3 pi_client.py --mode scp   (or --mode http)
"""

import os
import sys
import time
import json
import logging
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

# ─── Configuration (update these for your setup) ───────────────────
# Cloud server (Render) — prediction + dashboard
RENDER_URL     = "https://freshness-monitor.onrender.com"  # ← Your Render URL
HTTP_PREDICT   = f"{RENDER_URL}/predict"
HTTP_BARCODE   = f"{RENDER_URL}/barcode"

# Local PC server — file storage only
LOCAL_SERVER_IP   = "100.108.137.17"      # ← Your PC's Tailscale IP
LOCAL_SERVER_PORT = 5001                  # file_server.py port
LOCAL_UPLOAD_URL  = f"http://{LOCAL_SERVER_IP}:{LOCAL_SERVER_PORT}/upload"

SERVER_USER    = "Acer"               # ← Your Windows username
SCP_DEST_DIR   = r"d:\\POC project\\incoming"  # Destination on your PC
CAPTURE_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
CAPTURE_INTERVAL = 10                 # Seconds between captures
IMAGE_WIDTH    = 1920
IMAGE_HEIGHT   = 1080

# ─── Logging Setup ──────────────────────────────────────────────────
Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pi_client")


def ensure_dirs():
    """Create capture directory if it doesn't exist."""
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    logger.info(f"Capture directory: {CAPTURE_DIR}")


def capture_image_picamera() -> str:
    """
    Capture an image using PiCamera2 (Raspberry Pi OS Bullseye+).
    Returns the path to the saved image.
    """
    try:
        from picamera2 import Picamera2
    except ImportError:
        logger.error(
            "picamera2 not installed. Install with: sudo apt install python3-picamera2"
        )
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"capture_{timestamp}.jpg"
    filepath = os.path.join(CAPTURE_DIR, filename)

    camera = Picamera2()
    config = camera.create_still_configuration(
        main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)}
    )
    camera.configure(config)
    camera.start()
    time.sleep(2)  # warm-up time for auto-exposure
    camera.capture_file(filepath)
    camera.stop()
    camera.close()

    logger.info(f"📸 Captured: {filepath}")
    return filepath


def capture_image_libcamera() -> str:
    """
    Capture an image using libcamera-still CLI (fallback method).
    Works on Raspberry Pi OS without picamera2.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"capture_{timestamp}.jpg"
    filepath = os.path.join(CAPTURE_DIR, filename)

    cmd = [
        "libcamera-still",
        "-o", filepath,
        "--width", str(IMAGE_WIDTH),
        "--height", str(IMAGE_HEIGHT),
        "--nopreview",
        "-t", "2000",  # 2 second preview for auto-exposure
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"libcamera-still failed: {result.stderr}")
        return None

    logger.info(f"📸 Captured (libcamera): {filepath}")
    return filepath


def transfer_scp(filepath: str) -> bool:
    """
    Transfer an image to the local server via SCP.
    Requires SSH key-based authentication to be set up:
      ssh-keygen -t rsa
      ssh-copy-id <SERVER_USER>@<SERVER_IP>
    """
    filename = os.path.basename(filepath)
    dest = f"{SERVER_USER}@{SERVER_IP}:{SCP_DEST_DIR}\\{filename}"

    cmd = ["scp", "-o", "StrictHostKeyChecking=no", filepath, dest]
    logger.info(f"📤 SCP transfer: {filename} → {SERVER_IP}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        logger.info(f"✅ Transfer successful: {filename}")
        return True
    else:
        logger.error(f"❌ SCP failed: {result.stderr.strip()}")
        return False


def transfer_http(filepath: str) -> dict:
    """
    Transfer an image via HTTP POST:
      1. Send to Render cloud for prediction + dashboard
      2. Send to local PC for file storage
    """
    import requests

    filename = os.path.basename(filepath)

    # ── 1. Send to Render for prediction ──
    logger.info(f"Sending: {filename} -> {HTTP_BARCODE}")
    result = None
    try:
        with open(filepath, "rb") as f:
            response = requests.post(
                HTTP_BARCODE,
                files={"image": (filename, f, "image/jpeg")},
                timeout=30,
            )
        if response.status_code == 200:
            result = response.json()
            stage = result.get("stage_name", "unknown")
            confidence = result.get("confidence", 0)
            barcode_id = result.get("barcode_id", "")
            logger.info(f"  >> {stage} (confidence: {confidence:.1%})")
            if result.get("hex_colors"):
                logger.info(f"  >> Colors: {result['hex_colors']}")
            if barcode_id:
                logger.info(f"  >> QR: {RENDER_URL}/barcode/image/{barcode_id}")
        else:
            logger.error(f"Server error {response.status_code}: {response.text}")
    except requests.exceptions.ConnectionError:
        logger.error(f"Cannot connect to Render at {HTTP_BARCODE}")
    except Exception as e:
        logger.error(f"Render transfer failed: {e}")

    # ── 2. Send to local PC for storage ──
    try:
        with open(filepath, "rb") as f:
            resp = requests.post(
                LOCAL_UPLOAD_URL,
                files={"image": (filename, f, "image/jpeg")},
                timeout=10,
            )
        if resp.status_code == 200:
            logger.info(f"  📁 Saved to PC: {filename}")
        else:
            logger.warning(f"  PC storage error: {resp.status_code}")
    except Exception:
        logger.warning(f"  PC storage offline, skipping local save")

    return result


def transfer_paramiko(filepath: str) -> bool:
    """
    Transfer via SCP using paramiko (pure Python, no scp CLI needed).
    Useful if the Pi doesn't have OpenSSH scp installed.
    """
    try:
        import paramiko
    except ImportError:
        logger.error("paramiko not installed. Install with: pip3 install paramiko")
        return False

    filename = os.path.basename(filepath)
    remote_path = os.path.join(SCP_DEST_DIR, filename).replace("\\\\", "\\")

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(SERVER_IP, username=SERVER_USER)
        sftp = ssh.open_sftp()
        sftp.put(filepath, remote_path)
        sftp.close()
        ssh.close()
        logger.info(f"✅ Paramiko transfer successful: {filename}")
        return True
    except Exception as e:
        logger.error(f"❌ Paramiko transfer failed: {e}")
        return False


def run_continuous_capture(mode: str, camera_method: str):
    """
    Main loop: capture images and transfer them continuously.

    Args:
        mode: 'scp', 'http', or 'paramiko'
        camera_method: 'picamera2' or 'libcamera'
    """
    ensure_dirs()
    logger.info("=" * 50)
    logger.info("Freshness Monitor - Raspberry Pi Client")
    logger.info(f"   Mode     : {mode}")
    logger.info(f"   Camera   : {camera_method}")
    logger.info(f"   Render   : {RENDER_URL}")
    logger.info(f"   Local PC : {LOCAL_UPLOAD_URL}")
    logger.info(f"   Interval : {CAPTURE_INTERVAL}s")
    logger.info("=" * 50)

    capture_count = 0
    success_count = 0

    try:
        while True:
            # 1. Capture image
            if camera_method == "picamera2":
                filepath = capture_image_picamera()
            else:
                filepath = capture_image_libcamera()

            if filepath is None:
                logger.warning("Capture failed, retrying...")
                time.sleep(CAPTURE_INTERVAL)
                continue

            capture_count += 1

            # 2. Transfer image
            if mode == "scp":
                ok = transfer_scp(filepath)
            elif mode == "paramiko":
                ok = transfer_paramiko(filepath)
            elif mode == "http":
                result = transfer_http(filepath)
                ok = result is not None
                if ok:
                    # Save result locally for reference
                    result_file = filepath.replace(".jpg", "_result.json")
                    with open(result_file, "w") as f:
                        json.dump(result, f, indent=2)
            else:
                logger.error(f"Unknown mode: {mode}")
                break

            if ok:
                success_count += 1

            logger.info(
                f"📊 Stats: {success_count}/{capture_count} successful transfers"
            )

            # 3. Wait for next capture
            time.sleep(CAPTURE_INTERVAL)

    except KeyboardInterrupt:
        logger.info("\n🛑 Stopped by user")
        logger.info(f"📊 Final stats: {success_count}/{capture_count} successful")


def test_connection(mode: str):
    """Test connectivity to the server without capturing an image."""
    logger.info(f"🔍 Testing connection to {SERVER_IP} ({mode} mode)...")

    if mode == "http":
        import requests
        try:
            r = requests.get(f"http://{SERVER_IP}:{SERVER_PORT}/status", timeout=5)
            if r.status_code == 200:
                logger.info(f"✅ Server is reachable! Response: {r.json()}")
            else:
                logger.warning(f"⚠️  Server responded with: {r.status_code}")
        except Exception as e:
            logger.error(f"❌ Cannot reach server: {e}")
    else:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", f"{SERVER_USER}@{SERVER_IP}", "echo OK"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info("✅ SSH connection successful!")
        else:
            logger.error(f"❌ SSH failed: {result.stderr.strip()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="📊 Freshness Monitor — Raspberry Pi Client"
    )
    parser.add_argument(
        "--mode",
        choices=["scp", "http", "paramiko"],
        default="http",
        help="Transfer method: scp (SSH), http (POST to server), paramiko (Python SSH)",
    )
    parser.add_argument(
        "--camera",
        choices=["picamera2", "libcamera"],
        default="picamera2",
        help="Camera capture method",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=CAPTURE_INTERVAL,
        help=f"Seconds between captures (default: {CAPTURE_INTERVAL})",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test server connectivity without capturing",
    )
    args = parser.parse_args()

    CAPTURE_INTERVAL = args.interval

    if args.test:
        test_connection(args.mode)
    else:
        run_continuous_capture(args.mode, args.camera)
