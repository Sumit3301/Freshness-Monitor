#!/usr/bin/env python3
"""
Lightweight File Transfer Script for Raspberry Pi
================================================
Transfers existing image files from the Raspberry Pi captures directory to the Windows Desktop PC.
Integrates with file_server.py on the PC to check for and skip duplicates before uploading.

Requirements on the Pi:
  pip3 install requests

Usage:
  python3 transfer_files.py
"""

import os
import sys
import glob
import argparse
import requests

DEFAULT_SERVER_IP = "100.126.82.18"
DEFAULT_PORT = 5001
script_dir = os.path.dirname(os.path.abspath(__file__))
local_captures = os.path.join(script_dir, "captures")
if os.path.isdir(local_captures):
    DEFAULT_DIR = local_captures
else:
    DEFAULT_DIR = os.path.expanduser("~/Desktop/project/captures")

def main():
    parser = argparse.ArgumentParser(description="Transfer files from Raspberry Pi to Desktop (excluding duplicates).")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="Source directory on Pi containing images")
    parser.add_argument("--server", default=DEFAULT_SERVER_IP, help="PC Tailscale IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="PC File Server port")
    args = parser.parse_args()

    source_dir = os.path.abspath(os.path.expanduser(args.dir))
    server_url = f"http://{args.server}:{args.port}"

    print("=" * 60)
    print("🚀 File Transfer Script (Raspberry Pi -> Desktop)")
    print(f"   Source directory: {source_dir}")
    print(f"   Target server   : {server_url}")
    print("=" * 60)

    if not os.path.isdir(source_dir):
        print(f"❌ Error: Source directory '{source_dir}' does not exist!")
        print("   Please specify the correct directory using: python3 transfer_files.py --dir /path/to/your/captures")
        sys.exit(1)

    # Test server connection
    try:
        r = requests.get(f"{server_url}/status", timeout=5)
        if r.status_code == 200:
            status_data = r.json()
            print(f"✅ Connected to PC File Server!")
            print(f"   Files already stored on PC: {status_data.get('files_stored', 0)}")
        else:
            print(f"❌ Server returned status code {r.status_code}")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"❌ Connection Error: Cannot reach server at {server_url}/status.")
        print(f"   Please ensure:")
        print(f"     1. file_server.py is running on your PC.")
        print(f"     2. Tailscale is connected on both PC and Raspberry Pi.")
        print(f"     3. The server IP ({args.server}) is correct.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error connecting to server: {e}")
        sys.exit(1)

    # Find all JPEG files
    files = sorted(glob.glob(os.path.join(source_dir, "*.jpg")) + glob.glob(os.path.join(source_dir, "*.jpeg")))
    if not files:
        print("ℹ️ No JPEG images found in source directory.")
        return

    total = len(files)
    print(f"🔍 Found {total} image files to process.")

    uploaded = 0
    skipped = 0
    failed = 0

    for idx, filepath in enumerate(files, 1):
        filename = os.path.basename(filepath)
        print(f"[{idx}/{total}] Processing {filename}...", end=" ", flush=True)

        # Check if file exists on PC
        try:
            check_url = f"{server_url}/check/{filename}"
            check_resp = requests.get(check_url, timeout=5)
            if check_resp.status_code == 200 and check_resp.json().get("exists"):
                print("⏭️ Skipped (already exists)")
                skipped += 1
                continue
        except Exception as e:
            print(f"⚠️ Check failed ({e}), attempting upload anyway...")

        # Upload file
        try:
            upload_url = f"{server_url}/upload"
            with open(filepath, "rb") as f:
                upload_resp = requests.post(
                    upload_url,
                    files={"image": (filename, f, "image/jpeg")},
                    timeout=30
                )
            if upload_resp.status_code == 200:
                print("✅ Uploaded successfully")
                uploaded += 1
            else:
                print(f"❌ Failed (Server error {upload_resp.status_code})")
                failed += 1
        except Exception as e:
            print(f"❌ Failed ({e})")
            failed += 1

    print("=" * 60)
    print("📊 Transfer Summary:")
    print(f"   Total processed: {total}")
    print(f"   Uploaded       : {uploaded}")
    print(f"   Skipped (dups) : {skipped}")
    print(f"   Failed         : {failed}")
    print("=" * 60)

if __name__ == "__main__":
    main()
