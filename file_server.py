import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Local File Storage Server
=========================
Lightweight server that runs on your PC to receive and store images from the Pi.
Runs on port 5001 so it doesn't conflict with the main prediction server.

Usage:
  python file_server.py
"""

import os
from datetime import datetime
from flask import Flask, request, jsonify

import config

app = Flask(__name__)

STORAGE_DIR = config.INCOMING_DIR


@app.route("/upload", methods=["POST"])
def upload():
    """Receive and store an image from the Pi."""
    if "image" not in request.files:
        return jsonify({"error": "No image file. Use 'image' form field."}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    os.makedirs(STORAGE_DIR, exist_ok=True)
    save_path = os.path.join(STORAGE_DIR, file.filename)
    file.save(save_path)

    size_kb = os.path.getsize(save_path) / 1024
    print(f"📥 Saved: {file.filename} ({size_kb:.1f} KB)")

    return jsonify({
        "status": "saved",
        "filename": file.filename,
        "size_kb": round(size_kb, 1),
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/check/<filename>", methods=["GET"])
def check_file(filename):
    """Check if a file already exists in the storage directory."""
    os.makedirs(STORAGE_DIR, exist_ok=True)
    exists = os.path.exists(os.path.join(STORAGE_DIR, filename))
    return jsonify({"exists": exists})


@app.route("/status", methods=["GET"])
def status():
    """Check server status and file count."""
    os.makedirs(STORAGE_DIR, exist_ok=True)
    files = [f for f in os.listdir(STORAGE_DIR)
             if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))]
    return jsonify({
        "status": "running",
        "storage_dir": STORAGE_DIR,
        "files_stored": len(files),
    })


if __name__ == "__main__":
    os.makedirs(STORAGE_DIR, exist_ok=True)
    print("=" * 50)
    print("📁 Local File Storage Server")
    print(f"   Storing images in: {STORAGE_DIR}")
    print(f"   Upload:  POST http://0.0.0.0:5001/upload")
    print(f"   Status:  GET  http://0.0.0.0:5001/status")
    print("=" * 50)

    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
