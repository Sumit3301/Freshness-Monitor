#!/usr/bin/env python3
"""
Raspberry Pi Image Capture & Transfer Script
=============================================
This script runs on the Raspberry Pi and:
  1. Captures images from the Pi Camera at regular intervals
  2. Transfers them to the local server via SCP or HTTP POST
  3. Logs transfer status and server responses
  4. Streams live video to the server dashboard (--stream mode)

Requirements on the Pi:
  pip3 install picamera2 paramiko requests

Setup:
  1. Update the configuration below with your server's IP address
  2. Set up SSH key auth:  ssh-keygen && ssh-copy-id user@server_ip
  3. Run:  python3 pi_client.py --mode scp   (or --mode http)
  4. For live streaming:  python3 pi_client.py --stream
• Camera check (on Pi):  vcgencmd get_camera
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
HTTP_FRAME     = f"{RENDER_URL}/frame"   # live video frame endpoint

# Local PC server — file storage only
LOCAL_SERVER_IP   = "100.126.82.18"      # ← Your PC's Tailscale IP
LOCAL_SERVER_PORT = 5001                  # file_server.py port
LOCAL_UPLOAD_URL  = f"http://{LOCAL_SERVER_IP}:{LOCAL_SERVER_PORT}/upload"

SERVER_IP      = LOCAL_SERVER_IP      # Alias for SSH/SCP connections
SERVER_PORT    = LOCAL_SERVER_PORT    # Alias for SSH/SCP connections
SERVER_USER    = "Acer"               # ← Your Windows username
SCP_DEST_DIR   = r"d:\\POC project\\incoming"  # Destination on your PC
CAPTURE_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
CAPTURE_INTERVAL = 10                 # Seconds between captures
IMAGE_WIDTH    = 1920
IMAGE_HEIGHT   = 1440                 # 4:3 ratio matches stream (640x480)

# ─── Video Stream Settings ──────────────────────────────────────────
STREAM_WIDTH   = 640    # lower resolution for smooth streaming
STREAM_HEIGHT  = 480
STREAM_FPS     = 15     # target frames per second for live stream

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
    dest_path = os.path.join(SCP_DEST_DIR, filename).replace("\\\\", "\\")

    # Check duplicate on remote Windows machine over SSH before copying
    check_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{SERVER_USER}@{SERVER_IP}", f'if exist "{dest_path}" (exit 0) else (exit 1)']
    try:
        check_result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
        if check_result.returncode == 0:
            logger.info(f"⏭️ File already exists on server, skipping: {filename}")
            return True
    except Exception as e:
        logger.warning(f"⚠️ Failed to check duplicate status over SSH: {e}")

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
      2. Send to local PC for file storage (skips if duplicate)
    """
    import requests

    filename = os.path.basename(filepath)

    # ── Check duplicate on local PC first ──
    try:
        check_url = f"http://{LOCAL_SERVER_IP}:{LOCAL_SERVER_PORT}/check/{filename}"
        resp_check = requests.get(check_url, timeout=5)
        if resp_check.status_code == 200 and resp_check.json().get("exists"):
            logger.info(f"⏭️ File already exists on local PC, skipping: {filename}")
            return {"status": "skipped", "reason": "exists"}
    except Exception as e:
        logger.warning(f"  Failed to check duplicate status on local PC: {e}")

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
                if result.get("result_url"):
                     logger.info(f"  >> Page: {RENDER_URL}{result['result_url']}")
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

        # Check duplicate
        try:
            sftp.stat(remote_path)
            logger.info(f"⏭️ File already exists on local PC, skipping: {filename}")
            sftp.close()
            ssh.close()
            return True
        except IOError:
            # File does not exist, proceed with upload
            pass

        sftp.put(filepath, remote_path)
        sftp.close()
        ssh.close()
        logger.info(f"✅ Paramiko transfer successful: {filename}")
        return True
    except Exception as e:
        logger.error(f"❌ Paramiko transfer failed: {e}")
        return False


def stream_video_feed(
    mode: str,
    camera_method: str,
    fps: int = STREAM_FPS,
    frame_url: str = HTTP_FRAME,
    capture_every: int = 0
):
    """
    Continuously captures frames from the Pi camera and pushes them to
    POST /frame on the server, powering the /stream live-view webpage.
    
    If capture_every > 0, it will pause the stream every N seconds,
    capture a high-res image, transfer it via the specified mode,
    and then resume the stream.

    Args:
        mode          : transfer mode for high-res captures ('scp', 'http', 'paramiko')
        camera_method : 'picamera2' (default) or 'libcamera'
        fps           : target push rate (frames/second)
        frame_url     : server endpoint to POST frames to
        capture_every : seconds between high-res photo captures (0 to disable)
    """
    import requests
    import io
    import threading

    session = requests.Session()

    # ── Render Warm-Up Ping ───────────────────────────────────────────
    # If target is Render, send an initial ping to wake up the free tier service before streaming
    if RENDER_URL in frame_url:
        logger.info("⏳ Warming up Render cloud server before streaming...")
        try:
            session.get(f"{RENDER_URL}/status", timeout=15)
            logger.info("✅ Render web service is active & ready!")
        except Exception as e:
            logger.warning(f"⚠️ Render warm-up ping failed (server may still be starting): {e}")

    interval = 1.0 / max(1, fps)
    logger.info("=" * 50)
    logger.info("Freshness Monitor — Live Video Stream + Capture")
    logger.info(f"   Camera   : {camera_method}")
    logger.info(f"   Target   : {frame_url}")
    logger.info(f"   Rate     : {fps} fps  (interval={interval:.3f}s)")
    if capture_every > 0:
        logger.info(f"   HR Photo : Every {capture_every}s (Mode: {mode})")
    logger.info("=" * 50)
    logger.info("Tip: verify camera with  vcgencmd get_camera  on the Pi")

    last_hr_capture = time.time()
    last_warning_time = 0.0

    # ── Async Background Frame Pusher Thread ──────────────────────────
    # Prevents camera loop from blocking when Wi-Fi upload or server response is slow.
    frame_lock = threading.Lock()
    latest_frame_bytes = None
    stop_event = threading.Event()

    def _frame_pusher_worker():
        nonlocal last_warning_time
        while not stop_event.is_set():
            data_to_send = None
            with frame_lock:
                data_to_send = latest_frame_bytes

            if data_to_send is None:
                time.sleep(0.03)
                continue

            try:
                session.post(
                    frame_url,
                    data=data_to_send,
                    headers={"Content-Type": "image/jpeg"},
                    timeout=(3.0, 10.0),
                )
            except Exception as e:
                now = time.time()
                if now - last_warning_time > 10.0:
                    logger.warning(f"Frame push warning (network/cloud latency): {e}")
                    last_warning_time = now
                time.sleep(0.1)
            else:
                time.sleep(0.01)

    pusher_thread = threading.Thread(target=_frame_pusher_worker, daemon=True)
    pusher_thread.start()

    if camera_method == "picamera2":
        try:
            from picamera2 import Picamera2
        except ImportError:
            logger.error("picamera2 not installed. Run: sudo apt install python3-picamera2")
            sys.exit(1)

        import numpy as np, cv2

        camera = Picamera2()
        # Single config at FULL resolution ensures identical sensor mode
        # and field of view for both streaming and high-res captures.
        # Frames are resized in software for the stream output.
        cfg = camera.create_video_configuration(
            main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT), "format": "BGR888"}
        )
        camera.configure(cfg)
        camera.start()
        time.sleep(1)   # let auto-exposure settle
        logger.info("📷 Pi Camera started — streaming…")
        logger.info(f"   Sensor: {IMAGE_WIDTH}x{IMAGE_HEIGHT} → stream downscaled to {STREAM_WIDTH}x{STREAM_HEIGHT}")

        try:
            while True:
                t0 = time.time()

                # Check if it's time for a high-res capture
                # No reconfiguration needed — camera is already at full res!
                if capture_every > 0 and (t0 - last_hr_capture) >= capture_every:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filepath = os.path.join(CAPTURE_DIR, f"capture_{timestamp}.jpg")
                    ensure_dirs()
                    camera.capture_file(filepath)
                    logger.info(f"📸 Captured high-res: {filepath}")

                    # Upload it
                    if mode == "scp":
                        transfer_scp(filepath)
                    elif mode == "paramiko":
                        transfer_paramiko(filepath)
                    else:
                        transfer_http(filepath)

                    last_hr_capture = time.time()
                    t0 = time.time()

                # Capture full-res frame then resize for stream
                # picamera2 returns an RGB-ordered array even with BGR888 format,
                # so we swap channels for cv2 which expects BGR.
                frame_rgb = camera.capture_array()
                frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                frame_stream = cv2.resize(frame_bgr, (STREAM_WIDTH, STREAM_HEIGHT))

                # Encode at quality=60 to optimize payload size over RPi Wi-Fi link (~20KB vs ~60KB)
                ok, buf = cv2.imencode(".jpg", frame_stream, [cv2.IMWRITE_JPEG_QUALITY, 60])
                if ok:
                    with frame_lock:
                        latest_frame_bytes = buf.tobytes()

                elapsed = time.time() - t0
                sleep_for = max(0.0, interval - elapsed)
                time.sleep(sleep_for)

        except KeyboardInterrupt:
            logger.info("\n🛑 Stream stopped by user")
        finally:
            stop_event.set()
            try:
                camera.stop()
                camera.close()
            except:
                pass

    else:  # libcamera fallback — lower fps, uses CLI
        logger.warning("libcamera fallback: streaming will be slow (~1fps)")
        try:
            while True:
                t0 = time.time()
                filepath = capture_image_libcamera()
                if filepath:
                    with open(filepath, "rb") as f:
                        buf_bytes = f.read()
                    with frame_lock:
                        latest_frame_bytes = buf_bytes
                    os.remove(filepath)  # cleanup temp file
                elapsed = time.time() - t0
                time.sleep(max(0.0, interval - elapsed))
        except KeyboardInterrupt:
            logger.info("\n🛑 Stream stopped by user")
        finally:
            stop_event.set()


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
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream live video to the server /stream page (pushes JPEG frames to /frame)",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=STREAM_FPS,
        help=f"Target frames/second for --stream mode (default: {STREAM_FPS})",
    )
    parser.add_argument(
        "--stream-url",
        default=HTTP_FRAME,
        help=f"Server /frame endpoint URL for streaming (default: {HTTP_FRAME})",
    )
    parser.add_argument(
        "--capture-every",
        type=int,
        default=0,
        help="In --stream mode, capture a high-res photo every N seconds (default: 0 = disabled)",
    )
    args = parser.parse_args()

    CAPTURE_INTERVAL = args.interval

    if args.stream:
        stream_video_feed(
            mode=args.mode,
            camera_method=args.camera,
            fps=args.fps,
            frame_url=args.stream_url,
            capture_every=args.capture_every
        )
    elif args.test:
        test_connection(args.mode)
    else:
        run_continuous_capture(args.mode, args.camera)
