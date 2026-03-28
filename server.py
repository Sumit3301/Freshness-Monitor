import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Flask Inference Server
======================
Real-time freshness classification server with multi-stage detection.

Endpoints:
  POST /predict        — upload image, get stage classification + color data
  GET  /status         — server health + last prediction
  GET  /dashboard      — real-time web dashboard
  GET  /history        — JSON list of recent predictions
  POST /barcode        — upload image, get QR code with freshness stage info
  GET  /barcode/<id>   — retrieve a generated barcode image
  POST /frame          — Pi client pushes a raw JPEG frame here
  GET  /video_feed     — MJPEG stream composed of frames from the Pi camera
  GET  /stream         — standalone live video stream webpage

Usage:
  python server.py
"""

import os
import io
import json
import time
import uuid
import base64
import shutil
import threading
from datetime import datetime
from collections import deque

import numpy as np
import cv2
import joblib
import qrcode
from PIL import Image as PILImage
from flask import Flask, request, jsonify, render_template_string, send_file

import config
import database
from prepare_data import extract_features

# ─── Flask App ──────────────────────────────────────────────────────
app = Flask(__name__)

# ─── Global State ───────────────────────────────────────────────────
model = None
scaler = None
prediction_history = deque(maxlen=100)
result_store = {}          # barcode_id -> {result + image_base64}
server_start_time = None

# ─── Live Video Frame Buffer ─────────────────────────────────────────
# The Pi client pushes raw JPEG bytes here via POST /frame.
# GET /video_feed reads from this buffer to serve an MJPEG stream.
latest_frame: bytes = b""           # raw JPEG bytes of the most-recent frame
frame_lock = threading.Lock()       # protects latest_frame


def load_model():
    """Load the trained model and scaler."""
    global model, scaler
    if not os.path.exists(config.MODEL_PATH):
        print("Model not found! Run train_model.py first.")
        sys.exit(1)
    model = joblib.load(config.MODEL_PATH)
    scaler = joblib.load(config.SCALER_PATH)
    print(f"Model loaded: {config.MODEL_PATH}")


def classify_image(image_path: str) -> dict:
    """Classify a single image and return the result with stage info."""
    try:
        features = extract_features(image_path)
        numeric = {k: v for k, v in features.items() if not isinstance(v, str)}
        hex_values = {k: v for k, v in features.items() if isinstance(v, str)}

        feat_vector = np.array([numeric[k] for k in sorted(numeric.keys())]).reshape(1, -1)
        feat_scaled = scaler.transform(feat_vector)

        prediction = model.predict(feat_scaled)[0]
        probabilities = model.predict_proba(feat_scaled)[0]

        # Build per-stage probabilities
        stage_probs = {}
        for i, prob in enumerate(probabilities):
            stage_probs[config.LABEL_NAMES[i]] = float(prob)

        result = {
            "stage": int(prediction),
            "stage_name": config.LABEL_NAMES[prediction],
            "stage_color": config.STAGE_COLORS[prediction],
            "confidence": float(max(probabilities)),
            "stage_probabilities": stage_probs,
            "hex_colors": hex_values,
            "filename": os.path.basename(image_path),
            "timestamp": datetime.now().isoformat(),
        }

        prediction_history.appendleft(result)
        return result

    except Exception as e:
        return {
            "error": str(e),
            "filename": os.path.basename(image_path),
            "timestamp": datetime.now().isoformat(),
        }


def generate_qr_code(url: str, stage_color: str) -> bytes:
    """Generate a QR code containing the result page URL."""
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)

    # Convert hex to RGB tuple
    r = int(stage_color[1:3], 16)
    g = int(stage_color[3:5], 16)
    b = int(stage_color[5:7], 16)

    img = qr.make_image(fill_color=(r, g, b), back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ─── File Watcher ───────────────────────────────────────────────────
def watch_incoming_folder():
    """Watch incoming/ folder for SCP-delivered images."""
    os.makedirs(config.INCOMING_DIR, exist_ok=True)
    processed = set()

    print(f"Watching folder: {config.INCOMING_DIR}")

    while True:
        try:
            files = os.listdir(config.INCOMING_DIR)
            image_files = [
                f for f in files
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))
                and f not in processed
            ]

            for filename in image_files:
                filepath = os.path.join(config.INCOMING_DIR, filename)
                time.sleep(1)
                initial_size = os.path.getsize(filepath)
                time.sleep(0.5)
                if os.path.getsize(filepath) != initial_size:
                    continue

                print(f"\nNew image detected: {filename}")
                result = classify_image(filepath)

                if "error" not in result:
                    stage = result["stage_name"]
                    conf = result["confidence"]
                    print(f"   Classification: {stage} ({conf:.2%})")

                    # Save result JSON
                    result_path = os.path.join(
                        config.RESULTS_DIR,
                        f"{os.path.splitext(filename)[0]}_result.json"
                    )
                    os.makedirs(config.RESULTS_DIR, exist_ok=True)
                    with open(result_path, "w") as f:
                        json.dump(result, f, indent=2)

                    # Auto-generate barcode & result page (Latest Only)
                    base_url = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{config.SERVER_PORT}")
                    latest_url = f"{base_url.rstrip('/')}/result/latest"

                    # Read image as base64 for result page
                    with open(filepath, "rb") as img_f:
                        image_b64 = base64.b64encode(img_f.read()).decode("utf-8")

                    result["barcode_url"] = "/barcode/image/latest"
                    result["result_url"] = "/result/latest"

                    # Store ONLY the latest result in memory
                    result_store.clear()
                    result_store["latest"] = {
                        **result,
                        "image_base64": image_b64
                    }

                    # Persist to database
                    with open(filepath, "rb") as db_img_f:
                        database.save_prediction(result, db_img_f.read())

                    # Generate & Save QR png for latest
                    qr_bytes = generate_qr_code(latest_url, result.get("stage_color", "#333333"))
                    barcode_dir = config.BARCODE_DIR
                    os.makedirs(barcode_dir, exist_ok=True)
                    # We overwrite 'latest_qr.png' so the endpoint always serves it
                    barcode_path = os.path.join(barcode_dir, "latest_qr.png")
                    with open(barcode_path, "wb") as f:
                        f.write(qr_bytes)
                    print(f"   QR barcode updated: {barcode_path}")
                else:
                    print(f"   Error: {result['error']}")

                processed.add(filename)

        except Exception as e:
            print(f"Watcher error: {e}")

        time.sleep(config.WATCHER_POLL_INTERVAL)


# ─── Routes ─────────────────────────────────────────────────────────
@app.route("/predict", methods=["POST"])
def predict():
    """Upload image → get stage classification result."""
    if "image" not in request.files:
        return jsonify({"error": "No image file uploaded. Use 'image' form field."}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    temp_dir = os.path.join(config.BASE_DIR, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)
    file.save(temp_path)

    result = classify_image(temp_path)

    # Keep the image in incoming/ for records
    try:
        os.makedirs(config.INCOMING_DIR, exist_ok=True)
        shutil.move(temp_path, os.path.join(config.INCOMING_DIR, file.filename))
    except:
        pass

    if "error" in result:
        return jsonify(result), 500

    return jsonify(result)


@app.route("/barcode", methods=["POST"])
def barcode():
    """
    Upload image → get QR barcode with freshness stage encoded.

    Returns JSON with:
      - classification result
      - barcode_url: URL to retrieve the QR code image
      - barcode_base64: base64-encoded QR code PNG (for embedding)

    Usage:
      curl -X POST -F "image=@sample.jpg" http://localhost:5000/barcode
    """
    if "image" not in request.files:
        return jsonify({"error": "No image file uploaded. Use 'image' form field."}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    temp_dir = os.path.join(config.BASE_DIR, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)
    file.save(temp_path)

    # Read image as base64 for result page
    with open(temp_path, "rb") as img_f:
        image_b64 = base64.b64encode(img_f.read()).decode("utf-8")

    result = classify_image(temp_path)

    # Keep the image in incoming/ for records
    try:
        os.makedirs(config.INCOMING_DIR, exist_ok=True)
        shutil.move(temp_path, os.path.join(config.INCOMING_DIR, file.filename))
    except:
        pass

    if "error" in result:
        return jsonify(result), 500

    # Generate QR for 'latest' URL
    base_url = request.host_url.rstrip('/')
    latest_url = f"{base_url}/result/latest"

    # Generate QR barcode pointing to /result/latest
    qr_bytes = generate_qr_code(latest_url, result.get("stage_color", "#333333"))

    # Save as 'latest_qr.png'
    os.makedirs(config.BARCODE_DIR, exist_ok=True)
    barcode_path = os.path.join(config.BARCODE_DIR, "latest_qr.png")
    with open(barcode_path, "wb") as f:
        f.write(qr_bytes)

    # Build response
    result["barcode_url"] = "/barcode/image/latest"
    result["barcode_base64"] = base64.b64encode(qr_bytes).decode("utf-8")
    result["result_url"] = "/result/latest"

    # Store ONLY the latest result
    result_store.clear()
    result_store["latest"] = {
        **result,
        "image_base64": image_b64
    }

    # Persist to database
    database.save_prediction(result, base64.b64decode(image_b64))

    return jsonify(result)




@app.route("/barcode/image/<barcode_id>", methods=["GET"])
def get_barcode(barcode_id):
    """Retrieve the generated barcode image (usually 'latest')."""
    # Map 'latest' to the actual file
    if barcode_id == "latest":
        filename = "latest_qr.png"
    else:
        filename = f"{barcode_id}.png"
        
    barcode_path = os.path.join(config.BARCODE_DIR, filename)
    if not os.path.exists(barcode_path):
        return jsonify({"error": "Barcode not found"}), 404
    return send_file(barcode_path, mimetype="image/png")


@app.route("/result/<result_id>", methods=["GET"])
def show_result(result_id):
    """Interactive result page showing the LATEST image + classification."""
    # Always serve 'latest' if requested, or look it up (though we only store latest now)
    target_id = "latest" if result_id == "latest" else result_id
    
    data = result_store.get(target_id)
    if not data:
        # Fallback: if user requests specific ID but we only have latest, show latest?
        # Or distinct error? User wants "show the last image".
        # If 'latest' exists, we can show it with a note?
        # For now, let's just try to get 'latest' if nothing else found.
        data = result_store.get("latest")
        if not data:
             return "<h2>No result data available (waiting for upload...)</h2><p><a href='/dashboard'>Go to dashboard</a></p>", 404

    return RESULT_HTML.replace("{{DATA_JSON}}", json.dumps(data))


@app.route("/result/cleanup/<result_id>", methods=["POST"])
def cleanup_result(result_id):
    """Remove result data from memory when the page is closed."""
    if result_id in result_store:
        del result_store[result_id]
        return "ok", 200
    return "not found", 404


RESULT_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Classification Result</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: #0a0e1a;
            color: #e0e6f0;
            min-height: 100vh;
        }
        .page {
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            text-align: center;
            padding: 24px 0 16px;
        }
        .header h1 {
            font-size: 1.6rem;
            font-weight: 600;
            background: linear-gradient(135deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .header .ts {
            font-size: 0.85rem;
            color: #6b7280;
            margin-top: 6px;
        }

        /* Layout */
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-top: 16px;
        }
        @media (max-width: 700px) {
            .grid { grid-template-columns: 1fr; }
        }

        /* Card */
        .card {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 20px;
            backdrop-filter: blur(12px);
        }
        .card-title {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #6b7280;
            margin-bottom: 14px;
        }

        /* Image */
        .img-card { grid-column: 1; }
        .img-card img {
            width: 100%;
            border-radius: 12px;
            object-fit: cover;
        }

        /* Stage badge */
        .stage-card { grid-column: 2; }
        @media (max-width: 700px) { .stage-card { grid-column: 1; } }
        .stage-badge {
            display: flex;
            align-items: center;
            gap: 14px;
            padding: 16px;
            border-radius: 14px;
            background: rgba(255,255,255,0.05);
            margin-bottom: 18px;
        }
        .stage-dot {
            width: 48px; height: 48px;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%,100% { box-shadow: 0 0 0 0 rgba(255,255,255,0.3); }
            50%     { box-shadow: 0 0 18px 4px rgba(255,255,255,0.15); }
        }
        .stage-label { font-size: 1.3rem; font-weight: 600; }
        .stage-sub { font-size: 0.85rem; color: #9ca3af; }

        /* Confidence gauge */
        .gauge-wrap {
            margin: 18px 0;
            text-align: center;
        }
        .gauge-bar {
            height: 10px;
            border-radius: 5px;
            background: rgba(255,255,255,0.08);
            overflow: hidden;
        }
        .gauge-fill {
            height: 100%;
            border-radius: 5px;
            transition: width 1.2s ease;
        }
        .gauge-pct {
            font-size: 2rem;
            font-weight: 700;
            margin-top: 8px;
        }

        /* Stage probabilities */
        .prob-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
        }
        .prob-label {
            width: 130px;
            font-size: 0.8rem;
            color: #9ca3af;
            text-align: right;
            flex-shrink: 0;
        }
        .prob-bar {
            flex: 1;
            height: 8px;
            border-radius: 4px;
            background: rgba(255,255,255,0.06);
            overflow: hidden;
        }
        .prob-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 1s ease;
        }
        .prob-val {
            width: 50px;
            font-size: 0.8rem;
            font-weight: 600;
        }

        /* Colors */
        .color-row {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }
        .swatch {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 6px;
        }
        .swatch-circle {
            width: 46px; height: 46px;
            border-radius: 50%;
            border: 2px solid rgba(255,255,255,0.15);
        }
        .swatch-hex {
            font-size: 0.72rem;
            color: #9ca3af;
            font-family: monospace;
        }

        /* QR */
        .qr-wrap {
            text-align: center;
        }
        .qr-wrap img {
            width: 140px;
            border-radius: 10px;
            background: #fff;
            padding: 8px;
        }

        /* Bottom bar */
        .full-span { grid-column: 1 / -1; }
        .back-link {
            display: block;
            text-align: center;
            margin-top: 20px;
            color: #60a5fa;
            text-decoration: none;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
<div class="page">
    <div class="header">
        <h1>Freshness Classification Result</h1>
        <div class="ts" id="timestamp"></div>
    </div>

    <div class="grid">
        <!-- Image -->
        <div class="card img-card">
            <div class="card-title">Uploaded Image</div>
            <img id="srcImg" alt="source image">
        </div>

        <!-- Stage + Confidence -->
        <div class="card stage-card">
            <div class="card-title">Classification</div>
            <div class="stage-badge">
                <div class="stage-dot" id="dot"></div>
                <div>
                    <div class="stage-label" id="stageName"></div>
                    <div class="stage-sub" id="filename"></div>
                </div>
            </div>

            <div class="gauge-wrap">
                <div class="card-title">Confidence</div>
                <div class="gauge-bar"><div class="gauge-fill" id="gaugeFill"></div></div>
                <div class="gauge-pct" id="gaugePct"></div>
            </div>
        </div>

        <!-- Stage probabilities -->
        <div class="card full-span">
            <div class="card-title">Stage Probabilities</div>
            <div id="probs"></div>
        </div>

        <!-- Dominant colors -->
        <div class="card">
            <div class="card-title">Dominant Film Colors</div>
            <div class="color-row" id="colors"></div>
        </div>

        <!-- QR -->
        <div class="card">
            <div class="card-title">QR Barcode</div>
            <div class="qr-wrap">
                <img id="qrImg" alt="QR code">
                <div class="stage-sub" style="margin-top:8px" id="barcodeId"></div>
            </div>
        </div>
    </div>

    <a class="back-link" href="/dashboard">&larr; Back to Dashboard</a>
</div>

<script>
const STAGE_COLORS = ['#2ecc71', '#f1c40f', '#e67e22', '#e74c3c'];
const D = {{DATA_JSON}};

// Image
document.getElementById('srcImg').src = 'data:image/jpeg;base64,' + D.image_base64;

// Stage
document.getElementById('stageName').textContent = D.stage_name;
document.getElementById('filename').textContent = D.filename;
document.getElementById('dot').style.background = D.stage_color;
document.getElementById('timestamp').textContent = new Date(D.timestamp).toLocaleString();

// Confidence gauge
const pct = (D.confidence * 100).toFixed(1);
document.getElementById('gaugePct').textContent = pct + '%';
const fill = document.getElementById('gaugeFill');
fill.style.background = D.stage_color;
setTimeout(() => fill.style.width = pct + '%', 100);

// Probabilities
const probsEl = document.getElementById('probs');
Object.entries(D.stage_probabilities).forEach(([name, prob], i) => {
    const row = document.createElement('div');
    row.className = 'prob-row';
    const p = (prob * 100).toFixed(1);
    row.innerHTML = `
        <div class="prob-label">${name}</div>
        <div class="prob-bar"><div class="prob-fill" style="width:0;background:${STAGE_COLORS[i]}"></div></div>
        <div class="prob-val" style="color:${STAGE_COLORS[i]}">${p}%</div>
    `;
    probsEl.appendChild(row);
    setTimeout(() => row.querySelector('.prob-fill').style.width = p + '%', 150 + i * 120);
});

// Colors
const colorsEl = document.getElementById('colors');
Object.entries(D.hex_colors).forEach(([k, hex]) => {
    const s = document.createElement('div');
    s.className = 'swatch';
    s.innerHTML = `<div class="swatch-circle" style="background:${hex}"></div><div class="swatch-hex">${hex}</div>`;
    colorsEl.appendChild(s);
});

// QR
if (D.barcode_base64) {
    document.getElementById('qrImg').src = 'data:image/png;base64,' + D.barcode_base64;
}
document.getElementById('barcodeId').textContent = 'ID: ' + D.barcode_id;

// Cleanup on close
window.addEventListener("unload", function() {
    navigator.sendBeacon("/result/cleanup/" + D.barcode_id);
});
</script>
</body>
</html>
"""


# ─── Live Video Streaming ───────────────────────────────────────────

@app.route("/frame", methods=["POST"])
def receive_frame():
    """
    Pi client pushes one raw JPEG frame here (Content-Type: image/jpeg).
    The frame is buffered in memory and served by /video_feed.

    Usage (from pi_client.py):
      requests.post(SERVER_URL + "/frame", data=jpeg_bytes,
                    headers={"Content-Type": "image/jpeg"})
    """
    global latest_frame
    raw = request.get_data()          # raw JPEG bytes
    if not raw:
        return jsonify({"error": "No frame data"}), 400
    with frame_lock:
        latest_frame = raw
    return jsonify({"ok": True}), 200


def _generate_mjpeg():
    """Generator that yields MJPEG frames from the Pi-pushed buffer."""
    # A 1×1 dark placeholder shown when no Pi frame has arrived yet.
    PLACEHOLDER = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
        b"\x00\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4"
        b"\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff"
        b"\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04"
        b"\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"
        b"\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n"
        b"\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz"
        b"\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99"
        b"\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7"
        b"\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5"
        b"\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1"
        b"\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00"
        b"\x00?\x00\xfb\xd4P\x00\x00\x00\x1f\xff\xd9"
    )

    # Use OpenCV to generate a proper "waiting" placeholder image.
    def _make_waiting_frame():
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        img[:] = (20, 14, 10)   # dark background (BGR)
        cv2.putText(img, "Waiting for RPi camera...",
                    (90, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (120, 120, 180), 2, cv2.LINE_AA)
        cv2.putText(img, "POST /frame from pi_client.py",
                    (130, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (70, 70, 100), 1, cv2.LINE_AA)
        _, buf = cv2.imencode(".jpg", img)
        return buf.tobytes()

    waiting_frame = _make_waiting_frame()

    while True:
        with frame_lock:
            frame = latest_frame if latest_frame else waiting_frame
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
        time.sleep(0.05)   # ~20 fps cap; adjust freely


@app.route("/video_feed", methods=["GET"])
def video_feed():
    """MJPEG stream of RPi camera frames (pushed via POST /frame)."""
    from flask import Response
    response = Response(
        _generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
    # Critical for Render (nginx reverse proxy): disable response buffering so
    # frames are forwarded to the browser in real-time instead of being queued.
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response




@app.route("/latest_frame.jpg", methods=["GET"])
def latest_frame_jpg():
    """
    Returns the most-recent JPEG frame pushed by the Pi client.
    Designed for JS polling: browser fetches this URL every ~100 ms.
    Works through any reverse proxy (no long-lived connection needed).
    """
    from flask import Response
    with frame_lock:
        frame = latest_frame

    if not frame:
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        img[:] = (20, 14, 10)
        cv2.putText(img, "Waiting for RPi camera...",
                    (90, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (120, 120, 180), 2, cv2.LINE_AA)
        cv2.putText(img, "Run: python3 pi_client.py --stream",
                    (100, 235), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (70, 70, 100), 1, cv2.LINE_AA)
        _, buf = cv2.imencode(".jpg", img)
        frame = buf.tobytes()

    resp = Response(frame, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp

# ─── Stream Page HTML (JS-polling — works on any cloud host) ──────────────
VIDEO_STREAM_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Camera Stream — Freshness Monitor</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', sans-serif; background: #07091a; color: #e0e6f4;
               min-height: 100vh; display: flex; flex-direction: column; }
        .topbar { display: flex; align-items: center; justify-content: space-between;
                  padding: 14px 28px; background: rgba(255,255,255,0.03);
                  border-bottom: 1px solid rgba(255,255,255,0.07); backdrop-filter: blur(12px); }
        .topbar h1 { font-size: 1.1rem; font-weight: 600;
                     background: linear-gradient(90deg, #60a5fa, #a78bfa);
                     -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                     display: flex; align-items: center; gap: 10px; }
        .rec-dot { width: 10px; height: 10px; background: #ef4444; border-radius: 50%;
                   -webkit-text-fill-color: initial; animation: blink 1.2s ease-in-out infinite; }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.25} }
        .nav-links { display: flex; gap: 12px; }
        .nav-link { color: #94a3b8; text-decoration: none; font-size: 0.85rem;
                    padding: 6px 14px; border-radius: 6px;
                    border: 1px solid rgba(255,255,255,0.08); transition: all 0.2s; }
        .nav-link:hover { color: #e0e6f4; background: rgba(255,255,255,0.06); }
        .nav-link.active { color: #a78bfa; border-color: rgba(167,139,250,0.3); background: rgba(167,139,250,0.08); }
        .main { flex: 1; display: grid; grid-template-columns: 1fr 320px; }
        @media (max-width: 860px) {
            .main { grid-template-columns: 1fr; }
            .sidebar { border-left: none; border-top: 1px solid rgba(255,255,255,0.07); }
        }
        .stream-panel { background: #000; display: flex; align-items: center;
                         justify-content: center; position: relative; overflow: hidden; min-height: 360px; }
        #streamImg { width: 100%; height: 100%; object-fit: contain; display: block; }
        .overlay { position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%);
                   display: flex; gap: 8px; }
        .chip { padding: 4px 14px; border-radius: 20px; font-size: 0.75rem; font-weight: 600;
                backdrop-filter: blur(8px); background: rgba(0,0,0,0.6);
                border: 1px solid rgba(255,255,255,0.15); }
        .sidebar { border-left: 1px solid rgba(255,255,255,0.07);
                   background: rgba(255,255,255,0.015);
                   padding: 22px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 18px; }
        .section-label { font-size: 0.7rem; text-transform: uppercase;
                         letter-spacing: 1.5px; color: #475569; margin-bottom: 8px; }
        .status-pill { display: inline-flex; align-items: center; gap: 6px;
                       padding: 5px 13px; border-radius: 20px; font-size: 0.78rem; font-weight: 600; }
        .pill-live { background: rgba(16,185,129,0.12); border: 1px solid rgba(16,185,129,0.3); color: #34d399; }
        .pill-wait { background: rgba(100,116,139,0.12); border: 1px solid rgba(100,116,139,0.3); color: #94a3b8; }
        .pill-dot { width: 7px; height: 7px; border-radius: 50%; }
        .dot-live { background: #34d399; animation: blink 1.2s ease-in-out infinite; }
        .dot-wait { background: #94a3b8; }
        .stage-card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.07);
                      border-radius: 14px; padding: 16px; }
        .stage-badge { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
        .stage-dot { width: 42px; height: 42px; border-radius: 50%; flex-shrink: 0;
                     animation: pulse 2s ease-in-out infinite; }
        @keyframes pulse {
            0%,100%{box-shadow:0 0 0 0 rgba(255,255,255,0.2)}
            50%{box-shadow:0 0 14px 4px rgba(255,255,255,0.08)}
        }
        .stage-name { font-size: 1rem; font-weight: 700; }
        .stage-conf { font-size: 0.78rem; color: #94a3b8; margin-top: 2px; }
        .conf-bar { height: 7px; border-radius: 4px; background: rgba(255,255,255,0.07); overflow: hidden; margin-top: 2px; }
        .conf-fill { height: 100%; border-radius: 4px; transition: width 0.6s ease, background 0.4s; }
        .prob-list { display: flex; flex-direction: column; gap: 9px; }
        .prob-item { display: flex; align-items: center; gap: 8px; }
        .prob-name { font-size: 0.7rem; color: #94a3b8; width: 105px; flex-shrink: 0; }
        .prob-bar { flex: 1; height: 5px; border-radius: 3px; background: rgba(255,255,255,0.06); overflow: hidden; }
        .prob-fill { height: 100%; border-radius: 3px; transition: width 0.6s ease; }
        .prob-pct { font-size: 0.7rem; font-weight: 600; width: 36px; text-align: right; }
        .info-card { background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.06);
                     border-radius: 10px; padding: 13px 15px; font-size: 0.76rem; color: #64748b; line-height: 1.75; }
        .info-card code { background: rgba(255,255,255,0.06); padding: 1px 5px; border-radius: 4px;
                          font-family: monospace; color: #c4b5fd; font-size: 0.73rem; }
        #fps-badge { font-size: 0.68rem; color: #475569; margin-top: 4px; }
    </style>
</head>
<body>
<div class="topbar">
    <h1><span class="rec-dot"></span> Live Camera Feed</h1>
    <div class="nav-links">
        <a href="/dashboard" class="nav-link">Dashboard</a>
        <a href="/result/latest" class="nav-link">Latest Result</a>
        <a href="/gallery" class="nav-link">Gallery</a>
        <a href="/stream" class="nav-link active">Live Stream</a>
    </div>
</div>
<div class="main">
    <div class="stream-panel">
        <img id="streamImg" alt="RPi Camera" src="/latest_frame.jpg">
        <div class="overlay">
            <div class="chip" id="chipStage">Waiting...</div>
            <div class="chip" id="chipConf"></div>
        </div>
    </div>
    <div class="sidebar">
        <div>
            <div class="section-label">Camera Status</div>
            <div class="status-pill pill-wait" id="camPill">
                <span class="pill-dot dot-wait" id="pillDot"></span>
                <span id="camText">Waiting for Pi...</span>
            </div>
            <div id="fps-badge"></div>
        </div>
        <div class="stage-card">
            <div class="section-label">Latest Classification</div>
            <div class="stage-badge">
                <div class="stage-dot" id="stageDot" style="background:#334155"></div>
                <div>
                    <div class="stage-name" id="stageName">-</div>
                    <div class="stage-conf" id="stageConf">No prediction yet</div>
                </div>
            </div>
            <div class="section-label" style="margin-top:4px">Confidence</div>
            <div class="conf-bar"><div class="conf-fill" id="confFill" style="width:0%;background:#334155"></div></div>
        </div>
        <div>
            <div class="section-label">Stage Probabilities</div>
            <div class="prob-list" id="probList"></div>
        </div>
        <div class="info-card">
            Pi streams by running:<br>
            <code>python3 pi_client.py --stream</code><br><br>
            Camera check on Pi:<br>
            <code>vcgencmd get_camera</code>
        </div>
    </div>
</div>
<script>
const COLORS = ['#2ecc71','#f1c40f','#e67e22','#e74c3c'];
const imgEl  = document.getElementById('streamImg');

// JS-Polling stream: each request is a plain GET, works through any proxy.
let frameCount = 0, lastCheck = Date.now();

function pollFrame() {
    const url = '/latest_frame.jpg?t=' + Date.now();
    const next = new Image();
    next.onload = () => { imgEl.src = next.src; frameCount++; setTimeout(pollFrame, 100); };
    next.onerror = () => { setTimeout(pollFrame, 400); };
    next.src = url;
}
pollFrame();

setInterval(() => {
    const fps = (frameCount / ((Date.now() - lastCheck) / 1000)).toFixed(1);
    document.getElementById('fps-badge').textContent = fps + ' fps';
    frameCount = 0; lastCheck = Date.now();
}, 3000);

async function refreshStatus() {
    try {
        const data = await fetch('/status').then(r => r.json());
        const p = data.last_prediction;
        if (p) {
            document.getElementById('camPill').className = 'status-pill pill-live';
            document.getElementById('pillDot').className = 'pill-dot dot-live';
            document.getElementById('camText').textContent = 'RPi Camera Active';
            const c = p.stage_color || '#94a3b8';
            document.getElementById('stageDot').style.background = c;
            document.getElementById('stageName').textContent = p.stage_name;
            document.getElementById('stageConf').textContent = 'Confidence: ' + (p.confidence*100).toFixed(1) + '%';
            const fill = document.getElementById('confFill');
            fill.style.width = (p.confidence*100).toFixed(1) + '%'; fill.style.background = c;
            document.getElementById('chipStage').textContent = p.stage_name;
            document.getElementById('chipStage').style.color = c;
            document.getElementById('chipConf').textContent = (p.confidence*100).toFixed(1) + '%';
            const list = document.getElementById('probList');
            if (p.stage_probabilities) {
                list.innerHTML = '';
                Object.entries(p.stage_probabilities).forEach(([name, prob], i) => {
                    const col = COLORS[i] || '#94a3b8', pct = (prob*100).toFixed(1);
                    const el = document.createElement('div'); el.className = 'prob-item';
                    el.innerHTML = '<div class="prob-name">' + name + '</div>' +
                        '<div class="prob-bar"><div class="prob-fill" style="width:' + pct + '%;background:' + col + '"></div></div>' +
                        '<div class="prob-pct" style="color:' + col + '">' + pct + '%</div>';
                    list.appendChild(el);
                });
            }
        }
    } catch(e) {}
}
refreshStatus();
setInterval(refreshStatus, 2000);
</script>
</body>
</html>
"""


@app.route("/stream", methods=["GET"])
def stream_page():
    """Standalone live video stream webpage."""
    from flask import render_template_string
    return render_template_string(VIDEO_STREAM_HTML)


@app.route("/status", methods=["GET"])
def status():
    """Server health check."""
    from flask import request
    base_url = request.host_url.rstrip('/')
    
    uptime = (datetime.now() - server_start_time).total_seconds() if server_start_time else 0
    latest = result_store.get("latest")
    
    return jsonify({
        "status": "running",
        "model_loaded": model is not None,
        "uptime_seconds": int(uptime),
        "total_predictions": len(prediction_history),
        "last_prediction": prediction_history[0] if prediction_history else None,
        "watching_folder": config.INCOMING_DIR,
        "latest_qr_url": "/barcode/image/latest" if latest else None,
        "result_page_url": f"{base_url}/result/latest" if latest else None,
        "stages": config.LABEL_NAMES,
        "stage_colors": config.STAGE_COLORS,
    })


@app.route("/history", methods=["GET"])
def history():
    """Recent predictions with stage info."""
    limit = request.args.get("limit", 20, type=int)
    return jsonify({
        "predictions": list(prediction_history)[:limit],
        "total": len(prediction_history),
    })


@app.route("/dashboard", methods=["GET"])
def dashboard():
    """Real-time web dashboard with stages and barcode support."""
    return render_template_string(DASHBOARD_HTML)


@app.route("/gallery", methods=["GET"])
def gallery_page():
    """Historical image gallery UI."""
    return render_template_string(GALLERY_HTML)


@app.route("/image/<filename>", methods=["GET"])
def serve_image(filename):
    """Serve a raw image from the incoming directory."""
    try:
        # Prevent directory traversal
        safe_name = os.path.basename(filename)
        return send_file(os.path.join(config.INCOMING_DIR, safe_name))
    except Exception as e:
        return jsonify({"error": "Image not found"}), 404


@app.route("/api/gallery", methods=["GET"])
def api_gallery():
    """Return JSON list of recent classification results from the database."""
    limit = request.args.get("limit", 50, type=int)
    try:
        results = database.get_predictions(limit)
    except Exception as e:
        print(f"Error loading gallery from DB: {e}")
        results = []
    return jsonify({"predictions": results})


@app.route("/api/gallery/image/<int:prediction_id>", methods=["GET"])
def api_gallery_image(prediction_id):
    """Serve a captured image from the database by prediction ID."""
    result = database.get_image(prediction_id)
    if result is None:
        return jsonify({"error": "Image not found"}), 404
    image_bytes, mimetype = result
    return send_file(io.BytesIO(image_bytes), mimetype=mimetype)


# ─── Gallery HTML (History page) ──────────────────────────────────────────────
GALLERY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>History Gallery — Freshness Monitor</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', sans-serif; background: #07091a; color: #e0e6f4; min-height: 100vh; }
        
        .topbar { display: flex; align-items: center; justify-content: space-between;
                  padding: 14px 28px; background: rgba(255,255,255,0.03);
                  border-bottom: 1px solid rgba(255,255,255,0.07); backdrop-filter: blur(12px); position: sticky; top: 0; z-index: 10; }
        .topbar h1 { font-size: 1.1rem; font-weight: 600;
                     background: linear-gradient(90deg, #34d399, #10b981);
                     -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .nav-links { display: flex; gap: 12px; }
        .nav-link { color: #94a3b8; text-decoration: none; font-size: 0.85rem;
                    padding: 6px 14px; border-radius: 6px;
                    border: 1px solid rgba(255,255,255,0.08); transition: all 0.2s; }
        .nav-link:hover { color: #e0e6f4; background: rgba(255,255,255,0.06); }
        .nav-link.active { color: #34d399; border-color: rgba(52,211,153,0.3); background: rgba(52,211,153,0.08); }
        
        .container { max-width: 1400px; margin: 0 auto; padding: 30px; }
        .header { margin-bottom: 30px; }
        .header h2 { font-size: 1.8rem; font-weight: 700; margin-bottom: 8px; }
        .header p { color: #94a3b8; font-size: 0.95rem; }

        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 24px; }
        
        .card { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.07);
                border-radius: 16px; overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; }
        .card:hover { transform: translateY(-4px); box-shadow: 0 12px 24px rgba(0,0,0,0.4); border-color: rgba(255,255,255,0.15); }
        
        .img-container { width: 100%; aspect-ratio: 4/3; background: #000; overflow: hidden; position: relative; }
        .img-container img { width: 100%; height: 100%; object-fit: cover; transition: transform 0.3s; }
        .card:hover .img-container img { transform: scale(1.05); }
        
        .card-body { padding: 16px; }
        .card-stage { font-size: 1.1rem; font-weight: 700; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center; }
        .card-conf { font-size: 0.8rem; padding: 3px 8px; border-radius: 12px; background: rgba(255,255,255,0.1); }
        .card-time { color: #64748b; font-size: 0.8rem; margin-bottom: 12px; }
        
        .prob-bar { width: 100%; height: 6px; background: rgba(255,255,255,0.06); border-radius: 3px; overflow: hidden; margin-top: 10px; }
        .prob-fill { height: 100%; border-radius: 3px; }
        
        .empty-state { text-align: center; padding: 60px 20px; color: #64748b; font-size: 1.1rem; grid-column: 1 / -1; }
    </style>
</head>
<body>
    <div class="topbar">
        <h1>Historical Gallery</h1>
        <div class="nav-links">
            <a href="/dashboard" class="nav-link">Dashboard</a>
            <a href="/result/latest" class="nav-link">Latest Result</a>
            <a href="/gallery" class="nav-link active">Gallery</a>
            <a href="/stream" class="nav-link">Live Stream</a>
        </div>
    </div>
    
    <div class="container">
        <div class="header">
            <h2>High-Resolution Captures</h2>
            <p>Recent periodic snapshots and manually uploaded classifications.</p>
        </div>
        <div class="grid" id="galleryGrid">
            <div class="empty-state">Loading history...</div>
        </div>
    </div>

    <script>
        async function loadGallery() {
            try {
                const res = await fetch('/api/gallery?limit=50');
                const data = await res.json();
                const grid = document.getElementById('galleryGrid');
                
                if (!data.predictions || data.predictions.length === 0) {
                    grid.innerHTML = '<div class="empty-state">No historical captures found yet.</div>';
                    return;
                }
                
                grid.innerHTML = data.predictions.map(p => {
                    const color = p.stage_color || '#94a3b8';
                    const time = new Date(p.timestamp).toLocaleString();
                    const conf = (p.confidence * 100).toFixed(1);
                    return `
                        <div class="card">
                            <div class="img-container">
                                <a href="/api/gallery/image/${p.id}" target="_blank">
                                    <img src="/api/gallery/image/${p.id}" alt="${p.stage_name}" loading="lazy" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'100\\' height=\\'100\\'><rect width=\\'100\\' height=\\'100\\' fill=\\'%231e293b\\'/><text x=\\'50\\' y=\\'50\\' font-family=\\'Arial\\' font-size=\\'12\\' fill=\\'%2364748b\\' text-anchor=\\'middle\\' dy=\\'4\\'>Image Not Found</text></svg>'">
                                </a>
                            </div>
                            <div class="card-body">
                                <div class="card-stage" style="color: ${color}">
                                    ${p.stage_name || 'Unknown'}
                                    <span class="card-conf">${conf}%</span>
                                </div>
                                <div class="card-time">${time}</div>
                                <div class="prob-bar"><div class="prob-fill" style="width: ${conf}%; background: ${color}"></div></div>
                            </div>
                        </div>
                    `;
                }).join('');
            } catch (err) {
                document.getElementById('galleryGrid').innerHTML = '<div class="empty-state">Failed to load gallery data.</div>';
            }
        }
        
        loadGallery();
        setInterval(loadGallery, 30000); // refresh every 30s
    </script>
</body>
</html>
"""

# ─── Dashboard HTML ─────────────────────────────────────────────────────────
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Freshness Monitor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: #e0e0e0;
            min-height: 100vh;
        }
        .container { max-width: 1100px; margin: 0 auto; padding: 24px; }

        header { text-align: center; padding: 32px 0 24px; }
        header h1 {
            font-size: 2.2em;
            background: linear-gradient(90deg, #2ecc71, #f1c40f, #e67e22, #e74c3c);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 8px;
        }
        header .subtitle { color: #8888aa; font-size: 0.95em; }

        /* Stage indicator bar */
        .stage-bar {
            display: flex;
            gap: 4px;
            margin-bottom: 24px;
            border-radius: 12px;
            overflow: hidden;
        }
        .stage-segment {
            flex: 1;
            padding: 12px 8px;
            text-align: center;
            font-size: 0.75em;
            font-weight: 600;
            letter-spacing: 0.5px;
            opacity: 0.3;
            transition: all 0.5s ease;
        }
        .stage-segment.active { opacity: 1; transform: scaleY(1.1); }
        .stage-segment.s0 { background: #2ecc71; color: #fff; }
        .stage-segment.s1 { background: #f1c40f; color: #333; }
        .stage-segment.s2 { background: #e67e22; color: #fff; }
        .stage-segment.s3 { background: #e74c3c; color: #fff; }

        .status-bar { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
        .status-card {
            flex: 1; min-width: 160px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px; padding: 16px;
            backdrop-filter: blur(10px);
        }
        .status-card .label { font-size: 0.8em; color: #8888aa; text-transform: uppercase; letter-spacing: 1px; }
        .status-card .value { font-size: 1.6em; font-weight: 700; margin-top: 4px; }

        .upload-section {
            background: rgba(255,255,255,0.05);
            border: 2px dashed rgba(255,255,255,0.15);
            border-radius: 16px; padding: 32px;
            text-align: center; margin-bottom: 24px;
            transition: all 0.3s;
        }
        .upload-section:hover { border-color: #ffa07a; background: rgba(255,255,255,0.08); }
        .upload-section input[type="file"] { display: none; }
        .upload-btn {
            display: inline-block;
            background: linear-gradient(135deg, #6c5ce7, #a29bfe);
            color: white; padding: 12px 32px; border-radius: 8px;
            cursor: pointer; font-size: 1em; font-weight: 600; border: none;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .upload-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(108,92,231,0.3); }

        .result-card {
            background: rgba(255,255,255,0.06);
            border-radius: 16px; padding: 24px; margin-bottom: 24px;
            border: 1px solid rgba(255,255,255,0.1);
            display: none;
        }
        .result-card.show { display: block; animation: fadeIn 0.5s ease; }

        .result-header { display: flex; align-items: center; gap: 24px; margin-bottom: 20px; }
        .result-stage {
            font-size: 2em; font-weight: 800; padding: 16px 24px;
            border-radius: 12px; text-align: center; flex: 1;
        }
        .result-qr { text-align: center; }
        .result-qr img { width: 120px; height: 120px; border-radius: 8px; border: 2px solid rgba(255,255,255,0.2); }
        .result-qr a { display: block; margin-top: 6px; color: #a29bfe; font-size: 0.8em; text-decoration: none; }

        .stage-probs {
            display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap;
        }
        .stage-prob {
            flex: 1; min-width: 140px; padding: 10px;
            border-radius: 8px; text-align: center;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
        }
        .stage-prob .prob-name { font-size: 0.75em; color: #aaa; margin-bottom: 4px; }
        .stage-prob .prob-val { font-size: 1.4em; font-weight: 700; }
        .stage-prob .prob-bar { height: 4px; border-radius: 2px; margin-top: 6px; background: rgba(255,255,255,0.1); overflow: hidden; }
        .stage-prob .prob-fill { height: 100%; border-radius: 2px; transition: width 1s ease; }

        .hex-colors { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; justify-content: center; }
        .color-swatch {
            width: 40px; height: 40px; border-radius: 8px;
            border: 2px solid rgba(255,255,255,0.2); position: relative; cursor: pointer;
        }
        .color-swatch .tooltip {
            display: none; position: absolute; bottom: -24px; left: 50%;
            transform: translateX(-50%); font-size: 0.7em; white-space: nowrap; color: #aaa;
        }
        .color-swatch:hover .tooltip { display: block; }

        .history-section h2 { font-size: 1.3em; margin-bottom: 16px; color: #a29bfe; }
        .history-item {
            display: flex; align-items: center; gap: 16px;
            padding: 12px 16px; background: rgba(255,255,255,0.03);
            border-radius: 8px; margin-bottom: 8px;
            border: 1px solid rgba(255,255,255,0.05); transition: background 0.2s;
        }
        .history-item:hover { background: rgba(255,255,255,0.06); }
        .history-item .filename { flex: 1; font-size: 0.9em; }
        .history-item .badge { padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; }
        .history-item .time { color: #666; font-size: 0.8em; }

        .loading { display: none; text-align: center; padding: 24px; }
        .loading.show { display: block; }
        .spinner {
            width: 40px; height: 40px;
            border: 3px solid rgba(255,255,255,0.1);
            border-top-color: #a29bfe;
            border-radius: 50%; animation: spin 0.8s linear infinite;
            margin: 0 auto 12px;
        }

        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Freshness Monitor</h1>
            <p class="subtitle">Multi-stage film color change detection with barcode tracking</p>
            <div style="margin-top:14px; display: flex; gap: 12px; justify-content: center;">
                <a href="/stream" style="display:inline-block;padding:8px 20px;border-radius:8px;
                   background:linear-gradient(135deg,rgba(96,165,250,0.15),rgba(167,139,250,0.15));
                   border:1px solid rgba(167,139,250,0.3);color:#a78bfa;
                   text-decoration:none;font-size:0.88em;font-weight:600;
                   transition:all 0.2s;" onmouseover="this.style.background='linear-gradient(135deg,rgba(96,165,250,0.25),rgba(167,139,250,0.25))'" onmouseout="this.style.background='linear-gradient(135deg,rgba(96,165,250,0.15),rgba(167,139,250,0.15))'">
                   📷 Live Stream
                </a>
                <a href="/gallery" style="display:inline-block;padding:8px 20px;border-radius:8px;
                   background:linear-gradient(135deg,rgba(16,185,129,0.15),rgba(52,211,153,0.15));
                   border:1px solid rgba(52,211,153,0.3);color:#34d399;
                   text-decoration:none;font-size:0.88em;font-weight:600;
                   transition:all 0.2s;" onmouseover="this.style.background='linear-gradient(135deg,rgba(16,185,129,0.25),rgba(52,211,153,0.25))'" onmouseout="this.style.background='linear-gradient(135deg,rgba(16,185,129,0.15),rgba(52,211,153,0.15))'">
                   🖼️ History Gallery
                </a>
            </div>
        </header>

        <div class="stage-bar" id="stageBar">
            <div class="stage-segment s0" id="seg0">STAGE 1<br>Very Fresh</div>
            <div class="stage-segment s1" id="seg1">STAGE 2<br>Fresh</div>
            <div class="stage-segment s2" id="seg2">STAGE 3<br>Early Spoilage</div>
            <div class="stage-segment s3" id="seg3">STAGE 4<br>Spoiled</div>
        </div>

        <div class="status-bar">
            <div class="status-card">
                <div class="label">Server</div>
                <div class="value" id="serverStatus" style="color:#2ecc71;">Online</div>
            </div>
            <div class="status-card">
                <div class="label">Predictions</div>
                <div class="value" id="totalPredictions">0</div>
            </div>
            <div class="status-card">
                <div class="label">Last Stage</div>
                <div class="value" id="lastResult">-</div>
            </div>
             <div class="status-card" id="liveQrCard" style="display:none; text-align:center;">
                <div class="label">Live Result</div>
                <a href="/result/latest" target="_blank" id="liveQrLink">
                    <img id="liveQrImg" src="" style="width:60px; height:60px; border-radius:4px; margin-top:4px;">
                </a>
            </div>
        </div>

        <div class="upload-section" id="uploadSection">
            <p style="margin-bottom: 16px; color: #8888aa;">Drop an image or click to classify + generate barcode</p>
            <label class="upload-btn" for="fileInput">Upload Image</label>
            <input type="file" id="fileInput" accept="image/*">
        </div>

        <div class="loading" id="loading">
            <div class="spinner"></div>
            <p>Analyzing color changes...</p>
        </div>

        <div class="result-card" id="resultCard">
            <div class="result-header">
                <div class="result-stage" id="resultStage"></div>
                <div class="result-qr" id="resultQR"></div>
            </div>
            <div style="text-align:center; margin-bottom: 12px;">
                <span style="color:#8888aa;">Confidence: </span>
                <span id="confidenceText" style="font-weight:700;"></span>
            </div>
            <div class="stage-probs" id="stageProbs"></div>
            <div class="hex-colors" id="hexColors"></div>
        </div>

        <div class="history-section">
            <h2>Recent Classifications</h2>
            <div id="historyList"></div>
        </div>
    </div>

    <script>
        const stageColors = {'Stage 1 - Very Fresh':'#2ecc71', 'Stage 2 - Fresh':'#f1c40f',
                             'Stage 3 - Early Spoilage':'#e67e22', 'Stage 4 - Spoiled':'#e74c3c'};
        const stageShort = {'Stage 1 - Very Fresh':'S1', 'Stage 2 - Fresh':'S2',
                            'Stage 3 - Early Spoilage':'S3', 'Stage 4 - Spoiled':'S4'};

        const fileInput = document.getElementById('fileInput');
        const uploadSection = document.getElementById('uploadSection');

        uploadSection.addEventListener('dragover', (e) => { e.preventDefault(); uploadSection.style.borderColor = '#a29bfe'; });
        uploadSection.addEventListener('dragleave', () => { uploadSection.style.borderColor = 'rgba(255,255,255,0.15)'; });
        uploadSection.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadSection.style.borderColor = 'rgba(255,255,255,0.15)';
            if (e.dataTransfer.files.length) uploadImage(e.dataTransfer.files[0]);
        });
        fileInput.addEventListener('change', () => { if (fileInput.files.length) uploadImage(fileInput.files[0]); });

        async function uploadImage(file) {
            document.getElementById('loading').classList.add('show');
            document.getElementById('resultCard').classList.remove('show');
            const formData = new FormData();
            formData.append('image', file);
            try {
                const res = await fetch('/barcode', { method: 'POST', body: formData });
                const data = await res.json();
                showResult(data);
            } catch (err) { alert('Error: ' + err.message); }
            document.getElementById('loading').classList.remove('show');
        }

        function showResult(data) {
            const card = document.getElementById('resultCard');
            const stage = document.getElementById('resultStage');
            const confText = document.getElementById('confidenceText');
            const qrDiv = document.getElementById('resultQR');
            const probsDiv = document.getElementById('stageProbs');
            const hexDiv = document.getElementById('hexColors');

            card.classList.add('show');

            // Stage label
            const color = data.stage_color || '#aaa';
            stage.textContent = data.stage_name;
            stage.style.background = `linear-gradient(135deg, ${color}22, ${color}11)`;
            stage.style.color = color;
            stage.style.border = `2px solid ${color}44`;
            confText.textContent = (data.confidence * 100).toFixed(1) + '%';
            confText.style.color = color;

            // Stage bar highlight
            document.querySelectorAll('.stage-segment').forEach((s, i) => {
                s.classList.toggle('active', i === data.stage);
            });

            // QR code
            if (data.barcode_base64) {
                qrDiv.innerHTML = `<img src="data:image/png;base64,${data.barcode_base64}" alt="QR Code">
                    <a href="${data.barcode_url}" target="_blank">Download QR</a>`;
            }

            // Stage probabilities
            probsDiv.innerHTML = '';
            if (data.stage_probabilities) {
                Object.entries(data.stage_probabilities).forEach(([name, prob]) => {
                    const c = stageColors[name] || '#aaa';
                    probsDiv.innerHTML += `
                        <div class="stage-prob">
                            <div class="prob-name">${name}</div>
                            <div class="prob-val" style="color:${c}">${(prob*100).toFixed(1)}%</div>
                            <div class="prob-bar"><div class="prob-fill" style="width:${prob*100}%;background:${c}"></div></div>
                        </div>`;
                });
            }

            // Hex colors
            hexDiv.innerHTML = '';
            if (data.hex_colors) {
                Object.entries(data.hex_colors).forEach(([key, hex]) => {
                    const swatch = document.createElement('div');
                    swatch.className = 'color-swatch';
                    swatch.style.backgroundColor = hex;
                    swatch.innerHTML = `<span class="tooltip">${key}: ${hex}</span>`;
                    hexDiv.appendChild(swatch);
                });
            }
            refreshHistory();
        }

        async function refreshStatus() {
            try {
                const res = await fetch('/status');
                const data = await res.json();
                document.getElementById('totalPredictions').textContent = data.total_predictions;
                if (data.last_prediction) {
                    const el = document.getElementById('lastResult');
                    el.textContent = data.last_prediction.stage_name || '-';
                    el.style.color = data.last_prediction.stage_color || '#aaa';
                    el.style.fontSize = '1em';
                    // update stage bar
                    document.querySelectorAll('.stage-segment').forEach((s, i) => {
                        s.classList.toggle('active', i === data.last_prediction.stage);
                    });
                }
                
                // Live QR
                if (data.latest_qr_url) {
                    document.getElementById('liveQrCard').style.display = 'block';
                    document.getElementById('liveQrImg').src = data.latest_qr_url + '?t=' + new Date().getTime(); // burst cache
                    document.getElementById('liveQrLink').href = data.result_page_url;
                }
            } catch (err) { /* ignore */ }
        }

        async function refreshHistory() {
            try {
                const res = await fetch('/history?limit=10');
                const data = await res.json();
                const list = document.getElementById('historyList');
                list.innerHTML = data.predictions.map(p => {
                    const c = p.stage_color || '#aaa';
                    const short = stageShort[p.stage_name] || 'S?';
                    return `<div class="history-item">
                        <span class="filename">${p.filename}</span>
                        <span class="badge" style="background:${c}22;color:${c};border:1px solid ${c}44">${p.stage_name}</span>
                        <span class="time">${new Date(p.timestamp).toLocaleTimeString()}</span>
                    </div>`;
                }).join('');
            } catch (err) { /* ignore */ }
        }

        setInterval(() => { refreshStatus(); refreshHistory(); }, 5000);
        refreshStatus();
        refreshHistory();
    </script>
</body>
</html>
"""


def main():
    """Start the server with file watcher."""
    global server_start_time

    print("=" * 60)
    print("Freshness Classification - Multi-Stage Inference Server")
    print("=" * 60)

    load_model()

    os.makedirs(config.INCOMING_DIR, exist_ok=True)
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    os.makedirs(config.BARCODE_DIR, exist_ok=True)

    server_start_time = datetime.now()

    watcher = threading.Thread(target=watch_incoming_folder, daemon=True)
    watcher.start()

    port = int(os.environ.get("PORT", config.SERVER_PORT))

    print(f"\nServer starting on http://{config.SERVER_HOST}:{port}")
    print(f"Dashboard:  http://localhost:{port}/dashboard")
    print(f"Stream:     http://localhost:{port}/stream")
    print(f"Predict:    POST http://localhost:{port}/predict")
    print(f"Barcode:    POST http://localhost:{port}/barcode")
    print(f"Pi Frame:   POST http://localhost:{port}/frame")
    print(f"Watching:   {config.INCOMING_DIR}")
    print()

    app.run(
        host=config.SERVER_HOST,
        port=port,
        debug=False,
        threaded=True,
    )


# ─── Module-level init (for gunicorn: `gunicorn server:app`) ────────
load_model()
os.makedirs(config.INCOMING_DIR, exist_ok=True)
os.makedirs(config.RESULTS_DIR, exist_ok=True)
os.makedirs(config.BARCODE_DIR, exist_ok=True)
database.init_db()
server_start_time = datetime.now()


if __name__ == "__main__":
    main()
