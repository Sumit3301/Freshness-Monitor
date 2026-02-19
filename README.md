# Freshness Classification System 🦐🧀

A generic, item-agnostic computer vision system that classifies freshness stages based on RGB/HSV film color changes.

## 🚀 Features

- **Generic Classification**: Works with any reactive film (shrimp, paneer, etc.) by analyzing whole-image color shifts.
- **Dual-Server Architecture**:
  - **Cloud (Render)**: Hosts the prediction model, interactive dashboard, and live result pages.
  - **Local PC**: Acts as a secure file storage server for high-res image archiving via **Tailscale**.
- **Interactive Dashboard**:
  - Real-time status updates.
  - **Live QR Code**: Points to the latest prediction result.
  - **Auto-Cleanup**: Result data is cleared from memory when the tab is closed.
- **Raspberry Pi Client**: Captures images and sends them to both servers simultaneously.

## 🛠️ System Architecture

1.  **Raspberry Pi**: Captures images every 10s.
    - Sends to **Render** (`https://.../barcode`) for prediction.
    - Sends to **PC** (`http://100.x.x.x:5001/upload`) for storage.
2.  **Render Server**:
    - Analyzes image color.
    - Returns JSON + QR code link.
    - Hosts `/result/latest` — a beautiful, interactive page showing the current state.
3.  **Local PC Server**:
    - Simple Flask server (`file_server.py`).
    - Receives images via Tailscale VPN.
    - Saves to `D:\POC project\incoming`.

## 📦 Installation

### 1. Local PC (File Server)
This server stores the images.

1.  Install dependencies:
    ```bash
    pip install flask
    ```
2.  Run the server:
    ```bash
    python file_server.py
    ```
    *Runs on port 5001. Accessible via Tailscale IP.*

### 2. Cloud Server (Render)
This server runs the AI model.

1.  Push this repo to GitHub.
2.  Connect to **Render**.
3.  Deploy as a Web Service.
    - Build Command: `pip install -r requirements.txt`
    - Start Command: `gunicorn server:app`

### 3. Raspberry Pi Client
1.  Edit `pi_client.py` configuration:
    ```python
    # Render URL
    RENDER_URL     = "https://your-app-name.onrender.com"
    
    # Local PC (Tailscale IP)
    LOCAL_SERVER_IP   = "100.x.x.x"  # Your PC's Tailscale IP
    LOCAL_SERVER_PORT = 5001
    ```
2.  Run the client:
    ```bash
    python3 pi_client.py
    ```

## 📱 Usage

1.  **Open Dashboard**: Go to your Render URL (e.g., `https://freshness-monitor.onrender.com/dashboard`).
2.  **View Live Status**:
    - The dashboard updates automatically.
    - The **Live QR Code** links directly to the latest result page.
3.  **Scan QR**: Use your phone to scan the QR code on the dashboard or the printed output. It opens the interactive result page.
4.  **Local Files**: Images dropped into `incoming/` on the PC will also trigger processed results and QR codes.

## 📂 Project Structure

- `server.py`: Main prediction server (Cloud).
- `file_server.py`: Simple file storage server (Local).
- `pi_client.py`: Raspberry Pi image capture script.
- `train_model.py`: Script to train the color classifier.
- `prepare_data.py`: Feature extraction pipeline.
- `config.py`: Central configuration.

