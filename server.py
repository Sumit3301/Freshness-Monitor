import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

"""
Flask Inference Server
======================
Real-time freshness classification server with multi-stage detection.

Endpoints:
  POST /predict       — upload image, get stage classification + color data
  GET  /status        — server health + last prediction
  GET  /dashboard     — real-time web dashboard
  GET  /history       — JSON list of recent predictions
  POST /barcode       — upload image, get QR code with freshness stage info
  GET  /barcode/<id>  — retrieve a generated barcode image

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
from prepare_data import extract_features

# ─── Flask App ──────────────────────────────────────────────────────
app = Flask(__name__)

# ─── Global State ───────────────────────────────────────────────────
model = None
scaler = None
prediction_history = deque(maxlen=100)
server_start_time = None


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


def generate_qr_code(data: dict) -> bytes:
    """Generate a QR code containing freshness stage information."""
    qr_data = {
        "product": "freshness_classification",
        "stage": data.get("stage", -1),
        "stage_name": data.get("stage_name", "unknown"),
        "confidence": round(data.get("confidence", 0), 4),
        "dominant_colors": data.get("hex_colors", {}),
        "timestamp": data.get("timestamp", ""),
        "filename": data.get("filename", ""),
    }

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(json.dumps(qr_data, separators=(',', ':')))
    qr.make(fit=True)

    # Color the QR code based on stage
    stage_color = data.get("stage_color", "#333333")
    # Convert hex to RGB tuple
    r = int(stage_color[1:3], 16)
    g = int(stage_color[3:5], 16)
    b = int(stage_color[5:7], 16)

    img = qr.make_image(fill_color=(r, g, b), back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
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

                    # Auto-generate barcode
                    barcode_dir = config.BARCODE_DIR
                    os.makedirs(barcode_dir, exist_ok=True)
                    qr_bytes = generate_qr_code(result)
                    barcode_path = os.path.join(barcode_dir, f"{os.path.splitext(filename)[0]}_qr.png")
                    with open(barcode_path, "wb") as f:
                        f.write(qr_bytes)
                    print(f"   QR barcode saved: {barcode_path}")
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

    result = classify_image(temp_path)

    # Keep the image in incoming/ for records
    try:
        os.makedirs(config.INCOMING_DIR, exist_ok=True)
        shutil.move(temp_path, os.path.join(config.INCOMING_DIR, file.filename))
    except:
        pass

    if "error" in result:
        return jsonify(result), 500

    # Generate QR barcode
    qr_bytes = generate_qr_code(result)
    barcode_id = str(uuid.uuid4())[:8]

    # Save barcode
    os.makedirs(config.BARCODE_DIR, exist_ok=True)
    barcode_path = os.path.join(config.BARCODE_DIR, f"{barcode_id}.png")
    with open(barcode_path, "wb") as f:
        f.write(qr_bytes)

    # Build response
    result["barcode_id"] = barcode_id
    result["barcode_url"] = f"/barcode/image/{barcode_id}"
    result["barcode_base64"] = base64.b64encode(qr_bytes).decode("utf-8")

    return jsonify(result)


@app.route("/barcode/image/<barcode_id>", methods=["GET"])
def get_barcode(barcode_id):
    """Retrieve a generated barcode image by ID."""
    barcode_path = os.path.join(config.BARCODE_DIR, f"{barcode_id}.png")
    if not os.path.exists(barcode_path):
        return jsonify({"error": "Barcode not found"}), 404
    return send_file(barcode_path, mimetype="image/png")


@app.route("/status", methods=["GET"])
def status():
    """Server health check."""
    uptime = (datetime.now() - server_start_time).total_seconds() if server_start_time else 0
    return jsonify({
        "status": "running",
        "model_loaded": model is not None,
        "uptime_seconds": int(uptime),
        "total_predictions": len(prediction_history),
        "last_prediction": prediction_history[0] if prediction_history else None,
        "watching_folder": config.INCOMING_DIR,
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


# ─── Dashboard HTML ─────────────────────────────────────────────────
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
    print(f"Predict:    POST http://localhost:{port}/predict")
    print(f"Barcode:    POST http://localhost:{port}/barcode")
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
server_start_time = datetime.now()


if __name__ == "__main__":
    main()
