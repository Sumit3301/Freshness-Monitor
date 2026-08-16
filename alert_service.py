"""
Email Alert Service
===================
Sends email notifications when the freshness stage changes.
Alerts are sent ONCE per stage transition — repeated predictions of the
same stage do NOT trigger additional emails.

Relies on config.py for SMTP settings (loaded from environment variables).

Usage:
    from alert_service import alert_service
    alert_service.check_and_alert(result_dict)
"""

import smtplib
import time
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import config
import database


class AlertService:
    """Manages freshness stage alerts with transition-only logic."""

    # Stage-specific emoji and severity for email subject lines
    _STAGE_META = {
        0: {"emoji": "🟢", "severity": "INFO",     "label": "Fresh"},
        1: {"emoji": "🟡", "severity": "WARNING",  "label": "Spoiling"},
        2: {"emoji": "🔴", "severity": "CRITICAL", "label": "Spoiled"},
    }

    def __init__(self):
        self._last_alerted_stage: int | None = None
        self._lock = threading.Lock()
        # Load from DB on startup so we survive restarts
        try:
            self._last_alerted_stage = database.get_last_alerted_stage()
        except Exception:
            pass

        # ── Pi Heartbeat State ──────────────────────────────────────
        self._pi_last_seen: float = 0.0        # epoch timestamp of last Pi data
        self._pi_is_online: bool = False        # current known state
        self._pi_offline_email_sent_at: float = 0.0  # cooldown tracker
        self._pi_lock = threading.Lock()
        self._heartbeat_watchdog_started = False

    # ── Heartbeat Public API ────────────────────────────────────────

    def record_heartbeat(self):
        """Call this whenever the Pi sends any data (frame, image, prediction)."""
        with self._pi_lock:
            self._pi_last_seen = time.time()
            was_offline = not self._pi_is_online
            self._pi_is_online = True

        # If the Pi just came back, send a "back online" email
        if was_offline and self._pi_last_seen > 0:
            self._send_pi_online_email()

    def start_heartbeat_watchdog(self):
        """Start the background watchdog thread. Safe to call multiple times."""
        if self._heartbeat_watchdog_started:
            return
        self._heartbeat_watchdog_started = True
        t = threading.Thread(target=self._heartbeat_watchdog, daemon=True)
        t.start()
        print(f"  🫀 Pi heartbeat watchdog started "
              f"(timeout={config.PI_HEARTBEAT_TIMEOUT}s, "
              f"check every {config.PI_HEARTBEAT_CHECK_INTERVAL}s)")

    def pi_status(self) -> dict:
        """Return current Pi connectivity status for APIs."""
        with self._pi_lock:
            last_seen = self._pi_last_seen
            is_online = self._pi_is_online
        ago = time.time() - last_seen if last_seen > 0 else None
        return {
            "pi_online": is_online,
            "pi_last_seen": datetime.fromtimestamp(last_seen).isoformat() if last_seen > 0 else None,
            "pi_last_seen_seconds_ago": round(ago, 1) if ago is not None else None,
            "pi_heartbeat_timeout": config.PI_HEARTBEAT_TIMEOUT,
        }

    # ── Heartbeat Internal ──────────────────────────────────────────

    def _heartbeat_watchdog(self):
        """Background loop: checks Pi heartbeat and sends offline email if needed."""
        while True:
            time.sleep(config.PI_HEARTBEAT_CHECK_INTERVAL)

            with self._pi_lock:
                last_seen = self._pi_last_seen
                is_online = self._pi_is_online
                last_email = self._pi_offline_email_sent_at

            # Skip if Pi has never connected (server just started)
            if last_seen == 0.0:
                continue

            elapsed = time.time() - last_seen

            if elapsed > config.PI_HEARTBEAT_TIMEOUT and is_online:
                # Pi has gone offline
                with self._pi_lock:
                    self._pi_is_online = False

                # Respect cooldown to prevent email spam
                since_last_email = time.time() - last_email
                if since_last_email >= config.PI_OFFLINE_EMAIL_COOLDOWN:
                    self._send_pi_offline_email(elapsed)
                    with self._pi_lock:
                        self._pi_offline_email_sent_at = time.time()

    def _send_pi_offline_email(self, seconds_ago: float):
        """Send 'Pi went offline' email alert in a background thread."""
        if not config.ALERT_ENABLED or not config.ALERT_EMAIL_SENDER:
            print(f"  ⚠ Pi offline detected ({seconds_ago:.0f}s) but email not configured")
            return
        if not config.ALERT_EMAIL_RECIPIENTS:
            return

        print(f"  📧 Pi OFFLINE — sending alert email ({seconds_ago:.0f}s since last heartbeat)")

        def _send():
            subject = "🔴 ALERT: Raspberry Pi is OFFLINE — Freshness Monitor"
            html = self._build_pi_offline_html(seconds_ago)
            for recipient in config.ALERT_EMAIL_RECIPIENTS:
                try:
                    self._smtp_send(subject, html, [recipient])
                    print(f"    ✅ Pi offline alert sent to {recipient}")
                except Exception as e:
                    print(f"    ❌ Pi offline alert FAILED for {recipient}: {e}")

        threading.Thread(target=_send, daemon=True).start()

    def _send_pi_online_email(self):
        """Send 'Pi is back online' email alert in a background thread."""
        if not config.ALERT_ENABLED or not config.ALERT_EMAIL_SENDER:
            print("  ✅ Pi is BACK ONLINE")
            return
        if not config.ALERT_EMAIL_RECIPIENTS:
            return

        print("  📧 Pi BACK ONLINE — sending recovery email")

        def _send():
            subject = "🟢 RECOVERED: Raspberry Pi is back ONLINE — Freshness Monitor"
            html = self._build_pi_online_html()
            for recipient in config.ALERT_EMAIL_RECIPIENTS:
                try:
                    self._smtp_send(subject, html, [recipient])
                    print(f"    ✅ Pi online alert sent to {recipient}")
                except Exception as e:
                    print(f"    ❌ Pi online alert FAILED for {recipient}: {e}")

        threading.Thread(target=_send, daemon=True).start()

    # ── Pi Status Email Templates ───────────────────────────────────

    def _build_pi_offline_html(self, seconds_ago: float) -> str:
        """Build HTML email for Pi offline alert."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        minutes_ago = seconds_ago / 60

        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="margin:0;padding:0;background:#07091a;font-family:'Segoe UI',Arial,sans-serif;">
            <div style="max-width:560px;margin:0 auto;padding:30px 20px;">

                <!-- Header -->
                <div style="text-align:center;padding:24px 0;">
                    <div style="font-size:48px;margin-bottom:8px;">🔴</div>
                    <div style="font-size:24px;font-weight:700;color:#ef4444;">
                        Raspberry Pi Offline
                    </div>
                    <div style="font-size:13px;color:#64748b;margin-top:6px;">
                        No heartbeat received from the Pi camera
                    </div>
                </div>

                <!-- Details Card -->
                <div style="background:#0d1117;border:2px solid #dc262640;border-radius:14px;padding:24px;margin-bottom:20px;">
                    <table style="width:100%;border-collapse:collapse;">
                        <tr>
                            <td style="padding:10px 8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Status</td>
                            <td style="padding:10px 8px;font-size:15px;font-weight:700;color:#ef4444;">⬤ Offline</td>
                        </tr>
                        <tr>
                            <td style="padding:10px 8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Last Seen</td>
                            <td style="padding:10px 8px;font-size:14px;color:#e0e6f0;">{minutes_ago:.1f} minutes ago</td>
                        </tr>
                        <tr>
                            <td style="padding:10px 8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Detected At</td>
                            <td style="padding:10px 8px;font-size:14px;color:#e0e6f0;">{now}</td>
                        </tr>
                        <tr>
                            <td style="padding:10px 8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Timeout</td>
                            <td style="padding:10px 8px;font-size:14px;color:#e0e6f0;">{config.PI_HEARTBEAT_TIMEOUT}s</td>
                        </tr>
                    </table>
                </div>

                <!-- Troubleshooting -->
                <div style="background:#0d1117;border:1px solid #1e293b;border-radius:14px;padding:20px;margin-bottom:20px;">
                    <div style="font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:#666;margin-bottom:12px;">
                        🔧 Troubleshooting
                    </div>
                    <ul style="color:#94a3b8;font-size:13px;line-height:1.8;padding-left:18px;margin:0;">
                        <li>Check if the Pi is powered on and connected to the network</li>
                        <li>Verify Tailscale VPN is running: <code style="background:#1e293b;padding:2px 6px;border-radius:4px;color:#e0e6f0;">tailscale status</code></li>
                        <li>Check the capture service: <code style="background:#1e293b;padding:2px 6px;border-radius:4px;color:#e0e6f0;">sudo systemctl status freshness-capture</code></li>
                        <li>View logs: <code style="background:#1e293b;padding:2px 6px;border-radius:4px;color:#e0e6f0;">journalctl -u freshness-capture -f</code></li>
                        <li>Test connectivity: <code style="background:#1e293b;padding:2px 6px;border-radius:4px;color:#e0e6f0;">ping {config.LOCAL_SERVER_IP}</code></li>
                    </ul>
                </div>

                <!-- Footer -->
                <div style="text-align:center;padding:16px 0;border-top:1px solid #1e293b;">
                    <div style="font-size:12px;color:#475569;">
                        Freshness Monitor · Pi Heartbeat Alert
                    </div>
                    <div style="font-size:11px;color:#334155;margin-top:4px;">
                        You will receive a recovery email when the Pi reconnects.
                        Cooldown: {config.PI_OFFLINE_EMAIL_COOLDOWN // 60} min between repeated alerts.
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

    def _build_pi_online_html(self) -> str:
        """Build HTML email for Pi back online alert."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="margin:0;padding:0;background:#07091a;font-family:'Segoe UI',Arial,sans-serif;">
            <div style="max-width:560px;margin:0 auto;padding:30px 20px;">

                <!-- Header -->
                <div style="text-align:center;padding:24px 0;">
                    <div style="font-size:48px;margin-bottom:8px;">🟢</div>
                    <div style="font-size:24px;font-weight:700;color:#22c55e;">
                        Raspberry Pi is Back Online
                    </div>
                    <div style="font-size:13px;color:#64748b;margin-top:6px;">
                        Heartbeat restored — camera feed active
                    </div>
                </div>

                <!-- Details Card -->
                <div style="background:#0d1117;border:2px solid #22c55e40;border-radius:14px;padding:24px;">
                    <table style="width:100%;border-collapse:collapse;">
                        <tr>
                            <td style="padding:10px 8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Status</td>
                            <td style="padding:10px 8px;font-size:15px;font-weight:700;color:#22c55e;">⬤ Online</td>
                        </tr>
                        <tr>
                            <td style="padding:10px 8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Recovered At</td>
                            <td style="padding:10px 8px;font-size:14px;color:#e0e6f0;">{now}</td>
                        </tr>
                    </table>
                </div>

                <!-- Footer -->
                <div style="text-align:center;padding:16px 0;border-top:1px solid #1e293b;margin-top:20px;">
                    <div style="font-size:12px;color:#475569;">
                        Freshness Monitor · Pi Heartbeat Alert
                    </div>
                    <div style="font-size:11px;color:#334155;margin-top:4px;">
                        Monitoring will continue. You will be alerted again if the Pi goes offline.
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

    # ── Public API ──────────────────────────────────────────────────

    def check_and_alert(self, result: dict) -> str:
        """
        Check whether the prediction warrants an alert email.
        Returns the action taken: 'sent', 'skipped_same_stage',
        'skipped_disabled', or 'skipped_no_config'.
        """
        if not config.ALERT_ENABLED:
            return "skipped_disabled"

        if not config.ALERT_EMAIL_SENDER:
            return "skipped_no_config"

        if not config.ALERT_EMAIL_RECIPIENTS:
            return "skipped_no_recipients"

        stage = result.get("stage")
        if stage is None or stage not in config.ALERT_STAGES:
            return "skipped_not_alertable"

        with self._lock:
            if stage == self._last_alerted_stage:
                return "skipped_same_stage"
            self._last_alerted_stage = stage

        # Send in a background thread so we don't block the prediction pipeline
        t = threading.Thread(
            target=self._send_alert_emails,
            args=(result,),
            daemon=True,
        )
        t.start()
        return "sent"

    def send_test_email(self) -> dict:
        """Send a test email to verify SMTP configuration."""
        if not config.ALERT_EMAIL_SENDER:
            return {"success": False, "error": "SMTP sender not configured."}
        if not config.ALERT_EMAIL_RECIPIENTS:
            return {"success": False, "error": "No recipients configured."}

        subject = "✅ Freshness Monitor — Test Alert"
        body = self._build_test_html()

        try:
            self._smtp_send(subject, body, config.ALERT_EMAIL_RECIPIENTS)
            return {"success": True, "recipients": config.ALERT_EMAIL_RECIPIENTS}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Internal ────────────────────────────────────────────────────

    def _send_alert_emails(self, result: dict):
        """Build and send the alert email to all recipients."""
        stage = result["stage"]
        meta = self._STAGE_META.get(stage, self._STAGE_META[0])

        subject = (
            f"{meta['emoji']} {meta['severity']}: "
            f"{result.get('stage_name', 'Unknown Stage')} Detected "
            f"— Freshness Monitor"
        )

        html_body = self._build_alert_html(result, meta)

        any_success = False
        for recipient in config.ALERT_EMAIL_RECIPIENTS:
            status = "sent"
            try:
                self._smtp_send(subject, html_body, [recipient])
                print(f"  📧 Alert email sent to {recipient} — {result['stage_name']}")
                any_success = True
            except Exception as e:
                status = "failed"
                print(f"  ❌ Alert email FAILED for {recipient}: {e}")

            try:
                database.save_alert(
                    prediction_id=0,  # we don't have the DB row id here
                    stage=stage,
                    stage_name=result.get("stage_name", ""),
                    recipient=recipient,
                    subject=subject,
                    status=status,
                )
            except Exception as e:
                print(f"  ⚠ Failed to log alert to DB: {e}")

        if not any_success:
            with self._lock:
                self._last_alerted_stage = None

    def _smtp_send(self, subject: str, html_body: str, recipients: list[str]):
        """Send an HTML email via SMTP/TLS."""
        msg = MIMEMultipart("alternative")
        msg["From"] = config.ALERT_EMAIL_SENDER
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if config.ALERT_SMTP_USE_SSL:
            with smtplib.SMTP_SSL(config.ALERT_SMTP_HOST, config.ALERT_SMTP_PORT) as server:
                if config.ALERT_EMAIL_SENDER and config.ALERT_EMAIL_PASSWORD:
                    server.login(config.ALERT_EMAIL_SENDER, config.ALERT_EMAIL_PASSWORD)
                server.sendmail(config.ALERT_EMAIL_SENDER, recipients, msg.as_string())
        else:
            with smtplib.SMTP(config.ALERT_SMTP_HOST, config.ALERT_SMTP_PORT) as server:
                if config.ALERT_SMTP_USE_TLS:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                if config.ALERT_EMAIL_SENDER and config.ALERT_EMAIL_PASSWORD:
                    server.login(config.ALERT_EMAIL_SENDER, config.ALERT_EMAIL_PASSWORD)
                server.sendmail(config.ALERT_EMAIL_SENDER, recipients, msg.as_string())

    # ── HTML Email Templates ────────────────────────────────────────

    def _build_alert_html(self, result: dict, meta: dict) -> str:
        """Build a rich HTML email body for an alert."""
        stage_name = result.get("stage_name", "Unknown")
        stage_color = result.get("stage_color", "#94a3b8")
        confidence = result.get("confidence", 0)
        confidence_pct = f"{confidence * 100:.1f}%"
        filename = result.get("filename", "—")
        timestamp = result.get("timestamp", datetime.now().isoformat())
        ai_report = result.get("ai_report", "")

        # Dominant colors
        hex_colors = result.get("hex_colors", {})
        color_swatches = ""
        for key, hex_val in hex_colors.items():
            color_swatches += (
                f'<td style="padding:4px;">'
                f'<div style="width:36px;height:36px;border-radius:8px;'
                f'background:{hex_val};border:2px solid #333;display:inline-block;">'
                f'</div><br><span style="font-size:11px;color:#999;">{hex_val}</span></td>'
            )

        # Stage probabilities
        prob_rows = ""
        stage_colors_map = ["#2ecc71", "#f1c40f", "#e67e22", "#e74c3c"]
        stage_probs = result.get("stage_probabilities", {})
        for i, (name, prob) in enumerate(stage_probs.items()):
            bar_color = stage_colors_map[i] if i < 4 else "#94a3b8"
            bar_width = f"{prob * 100:.0f}"
            prob_rows += f"""
            <tr>
                <td style="padding:6px 8px;font-size:13px;color:#ccc;">{name}</td>
                <td style="padding:6px 8px;width:200px;">
                    <div style="background:#1e1e2e;border-radius:4px;overflow:hidden;height:10px;">
                        <div style="width:{bar_width}%;background:{bar_color};height:100%;border-radius:4px;"></div>
                    </div>
                </td>
                <td style="padding:6px 8px;font-size:13px;font-weight:700;color:{bar_color};">{prob*100:.1f}%</td>
            </tr>"""

        # AI report section
        ai_section = ""
        if ai_report and "unavailable" not in ai_report.lower():
            ai_section = f"""
            <tr><td colspan="3" style="padding:20px 0 8px;">
                <div style="font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:#666;">🤖 AI Assessment</div>
            </td></tr>
            <tr><td colspan="3" style="padding:0 0 20px;">
                <div style="background:#111827;border:1px solid #1f2937;border-radius:10px;padding:16px;
                            font-size:14px;line-height:1.7;color:#d1d5db;">
                    {ai_report}
                </div>
            </td></tr>"""

        # Dashboard link
        base_url = ""
        try:
            import os
            base_url = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{config.SERVER_PORT}")
        except Exception:
            pass
        dashboard_link = f"{base_url}/dashboard" if base_url else "#"

        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="margin:0;padding:0;background:#07091a;font-family:'Segoe UI',Arial,sans-serif;">
            <div style="max-width:600px;margin:0 auto;padding:20px;">

                <!-- Header -->
                <div style="text-align:center;padding:24px 0;">
                    <div style="font-size:32px;margin-bottom:8px;">{meta['emoji']}</div>
                    <div style="font-size:22px;font-weight:700;color:#e0e6f0;">Freshness Alert</div>
                    <div style="font-size:13px;color:#64748b;margin-top:4px;">Stage transition detected</div>
                </div>

                <!-- Stage Badge -->
                <div style="background:linear-gradient(135deg,{stage_color}15,{stage_color}08);
                            border:2px solid {stage_color}40;border-radius:16px;padding:24px;
                            text-align:center;margin-bottom:20px;">
                    <div style="font-size:28px;font-weight:800;color:{stage_color};">{stage_name}</div>
                    <div style="font-size:14px;color:#94a3b8;margin-top:8px;">
                        Confidence: <strong style="color:{stage_color};">{confidence_pct}</strong>
                    </div>
                </div>

                <!-- Details Card -->
                <div style="background:#0d1117;border:1px solid #1e293b;border-radius:14px;padding:20px;">
                    <table style="width:100%;border-collapse:collapse;">
                        <tr>
                            <td style="padding:8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Image</td>
                            <td colspan="2" style="padding:8px;font-size:14px;color:#e0e6f0;">{filename}</td>
                        </tr>
                        <tr>
                            <td style="padding:8px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:1px;">Time</td>
                            <td colspan="2" style="padding:8px;font-size:14px;color:#e0e6f0;">{timestamp}</td>
                        </tr>

                        <!-- Probabilities -->
                        <tr><td colspan="3" style="padding:16px 0 8px;">
                            <div style="font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:#666;">Stage Probabilities</div>
                        </td></tr>
                        {prob_rows}

                        <!-- Dominant Colors -->
                        <tr><td colspan="3" style="padding:16px 0 8px;">
                            <div style="font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:#666;">Dominant Film Colors</div>
                        </td></tr>
                        <tr><td colspan="3" style="padding:4px;">
                            <table><tr>{color_swatches}</tr></table>
                        </td></tr>

                        {ai_section}
                    </table>
                </div>

                <!-- Action Button -->
                <div style="text-align:center;margin:24px 0;">
                    <a href="{dashboard_link}" style="display:inline-block;padding:12px 32px;
                       background:linear-gradient(135deg,#6c5ce7,#a29bfe);color:#fff;
                       text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;">
                        Open Dashboard →
                    </a>
                </div>

                <!-- Footer -->
                <div style="text-align:center;padding:16px 0;border-top:1px solid #1e293b;">
                    <div style="font-size:12px;color:#475569;">
                        Freshness Classification System · Automated Alert
                    </div>
                    <div style="font-size:11px;color:#334155;margin-top:4px;">
                        This alert was triggered by a stage transition. You will not receive another
                        email until the classification changes to a different stage.
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

    def _build_test_html(self) -> str:
        """Build a simple test email body."""
        return """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="margin:0;padding:0;background:#07091a;font-family:'Segoe UI',Arial,sans-serif;">
            <div style="max-width:500px;margin:0 auto;padding:40px 20px;text-align:center;">
                <div style="font-size:48px;margin-bottom:16px;">✅</div>
                <div style="font-size:24px;font-weight:700;color:#e0e6f0;margin-bottom:8px;">
                    Email Configuration Verified
                </div>
                <div style="font-size:14px;color:#94a3b8;line-height:1.6;margin-bottom:24px;">
                    Your Freshness Monitor alert system is working correctly.
                    You will receive email notifications whenever the freshness
                    stage transitions (e.g., Fresh → Early Spoilage).
                </div>
                <div style="background:#0d1117;border:1px solid #1e293b;border-radius:12px;padding:16px;">
                    <table style="width:100%;text-align:left;">
                        <tr>
                            <td style="padding:6px;font-size:12px;color:#64748b;">SMTP Host</td>
                            <td style="padding:6px;font-size:13px;color:#e0e6f0;">""" + config.ALERT_SMTP_HOST + """</td>
                        </tr>
                        <tr>
                            <td style="padding:6px;font-size:12px;color:#64748b;">Sender</td>
                            <td style="padding:6px;font-size:13px;color:#e0e6f0;">""" + config.ALERT_EMAIL_SENDER + """</td>
                        </tr>
                        <tr>
                            <td style="padding:6px;font-size:12px;color:#64748b;">Recipients</td>
                            <td style="padding:6px;font-size:13px;color:#e0e6f0;">""" + ", ".join(config.ALERT_EMAIL_RECIPIENTS) + """</td>
                        </tr>
                    </table>
                </div>
                <div style="font-size:11px;color:#334155;margin-top:20px;">
                    Freshness Classification System · Test Alert
                </div>
            </div>
        </body>
        </html>
        """


# ── Module-level singleton ──────────────────────────────────────────
alert_service = AlertService()
