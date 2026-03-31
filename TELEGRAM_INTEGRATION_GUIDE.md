# Telegram Notification Integration Guide

This guide explains how to manually integrate the Telegram notification system into your existing `server.py` file.

## Quick Setup

If you want to quickly get started:

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set up your Telegram bot** (see [setup_telegram.md](setup_telegram.md))

3. **Set environment variables:**
   ```bash
   export TELEGRAM_BOT_TOKEN="your_bot_token_here"
   export TELEGRAM_CHAT_ID="your_chat_id_here"
   ```

4. **Apply the integration** (see below)

---

## Manual Integration Steps

### Step 1: Add Imports to server.py

At the top of `server.py`, after the existing imports, add:

```python
# Telegram Notification System
import asyncio
from notification_system import init_notifier, send_notification_sync

# Global notifier instance
notifier = None
```

### Step 2: Modify classify_image() Function

Find the `classify_image()` function. After this line:
```python
prediction_history.appendleft(result)
```

Add the following code:

```python
        # Send Telegram notification for concerning stages
        if notifier and notifier.enabled:
            stage = result.get('stage', 0)
            if stage in getattr(config, 'TELEGRAM_ALERT_ON_STAGES', [2, 3]):
                try:
                    send_notification_sync(
                        notifier,
                        notifier.send_freshness_alert(
                            stage=stage,
                            stage_name=result['stage_name'],
                            confidence=result['confidence'],
                            filename=result['filename'],
                            image_path=image_path if getattr(config, 'TELEGRAM_SEND_PHOTO', True) else None,
                            stage_probabilities=result.get('stage_probabilities'),
                            hex_colors=result.get('hex_colors')
                        )
                    )
                    print(f\"   Telegram notification sent for {result['stage_name']}\")
                except Exception as e:
                    print(f\"   Telegram notification error: {e}\")
```

### Step 3: Add API Endpoints

Add these new API endpoints before the `main()` function:

```python
@app.route("/api/test-notification", methods=["POST"])
def test_notification():
    """Test Telegram notification system."""
    if not notifier or not notifier.enabled:
        return jsonify({
            "error": "Telegram notifications not configured",
            "message": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables"
        }), 503
    
    try:
        success = send_notification_sync(notifier, notifier.send_test_notification())
        if success:
            return jsonify({
                "success": True,
                "message": "Test notification sent successfully!"
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to send test notification"
            }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/notification-status", methods=["GET"])
def notification_status():
    """Get Telegram notification system status."""
    if not notifier:
        return jsonify({
            "enabled": False,
            "configured": False,
            "message": "Notifier not initialized"
        })
    
    return jsonify({
        "enabled": notifier.enabled,
        "configured": bool(notifier.bot_token and notifier.chat_id),
        "bot_token_set": bool(notifier.bot_token),
        "chat_id_set": bool(notifier.chat_id),
        "alert_stages": getattr(config, 'TELEGRAM_ALERT_ON_STAGES', [2, 3]),
        "send_photos": getattr(config, 'TELEGRAM_SEND_PHOTO', True)
    })
```

### Step 4: Initialize Notifier in main()

Find the `main()` function. After the `load_model()` call, add:

```python
    # Initialize Telegram notifier
    global notifier
    if getattr(config, 'TELEGRAM_ENABLED', True):
        notifier = init_notifier()
        if notifier and notifier.enabled:
            print("✅ Telegram notifications: ENABLED")
            print(f"   Chat ID: {notifier.chat_id}")
            
            # Send startup notification if configured
            if getattr(config, 'TELEGRAM_NOTIFY_ON_STARTUP', True):
                try:
                    send_notification_sync(
                        notifier,
                        notifier.send_system_health(
                            status="starting",
                            uptime_seconds=0,
                            total_predictions=0,
                            model_loaded=True
                        )
                    )
                    print("   Startup notification sent")
                except Exception as e:
                    print(f"   ⚠️  Failed to send startup notification: {e}")
        else:
            print("⚠️  Telegram notifications: DISABLED (check configuration)")
    else:
        print("Telegram notifications: DISABLED (via config)")
```

### Step 5: Initialize for Production (Gunicorn)

Find the module-level initialization section (at the bottom of server.py, before `if __name__ == "__main__":`).

After these lines:
```python
server_start_time = datetime.now()
```

Add:

```python
# Initialize Telegram notifier for gunicorn/production
if getattr(config, 'TELEGRAM_ENABLED', True):
    notifier = init_notifier()
```

---

## Testing the Integration

### 1. Test Notification Status

```bash
curl http://localhost:5000/api/notification-status
```

Expected response:
```json
{
  "enabled": true,
  "configured": true,
  "bot_token_set": true,
  "chat_id_set": true,
  "alert_stages": [2, 3],
  "send_photos": true
}
```

### 2. Send Test Notification

```bash
curl -X POST http://localhost:5000/api/test-notification
```

You should receive a test message in Telegram!

### 3. Test with Real Image

Upload an image that triggers Stage 3 or 4:

```bash
curl -X POST -F "image=@spoiled_sample.jpg" http://localhost:5000/barcode
```

Check your Telegram for the freshness alert!

---

## Troubleshooting

### "Telegram notifications: DISABLED"

**Problem:** Notifier is disabled even though configuration is set.

**Solutions:**
- Check environment variables are set: `echo $TELEGRAM_BOT_TOKEN`
- Verify bot token format (should be like `1234567890:ABC...`)
- Ensure `python-telegram-bot` is installed: `pip install python-telegram-bot`
- Check config.py: `TELEGRAM_ENABLED` should be `True`

### No Notifications Received

**Problem:** Server runs but no Telegram messages arrive.

**Solutions:**
- Test with `/api/test-notification` endpoint first
- Verify Chat ID is correct (use `@userinfobot` to get it)
- Check server logs for error messages
- Ensure bot is not blocked in Telegram
- Verify stage threshold: only Stage 2 and 3 trigger notifications by default

### Import Error

**Problem:** `ModuleNotFoundError: No module named 'telegram'`

**Solution:**
```bash
pip install python-telegram-bot==20.7
```

### Async Warnings

**Problem:** Warnings about event loops in console.

**Solution:** This is normal and can be ignored. Notifications will still work. To suppress warnings, upgrade to Python 3.9+.

---

## Configuration Options

All settings are in `config.py`:

```python
# Basic Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", None)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", None)
TELEGRAM_ENABLED = True

# Notification Triggers
TELEGRAM_ALERT_ON_STAGES = [2, 3]  # Which stages trigger notifications
TELEGRAM_PHOTO_MIN_STAGE = 2       # Minimum stage to send photos
TELEGRAM_SEND_PHOTO = True         # Include photos in notifications

# Advanced
TELEGRAM_NOTIFY_ON_STARTUP = True           # Send notification on server start
TELEGRAM_DAILY_SUMMARY_ENABLED = True       # Daily summary reports
TELEGRAM_DAILY_SUMMARY_TIME = "18:00"       # Time for daily summary
TELEGRAM_HEALTH_CHECK_INTERVAL = 3600       # Health check interval (seconds)
```

---

## Advanced Features

### Daily Summaries

To enable daily summaries, you can add a scheduled task. Install the `schedule` library:

```bash
pip install schedule
```

Then add this code to `server.py`:

```python
import schedule

def send_daily_summary():
    """Send daily summary of predictions."""
    if notifier and notifier.enabled:
        predictions = list(prediction_history)
        send_notification_sync(notifier, notifier.send_daily_summary(predictions))

# Schedule daily summary
if getattr(config, 'TELEGRAM_DAILY_SUMMARY_ENABLED', True):
    summary_time = getattr(config, 'TELEGRAM_DAILY_SUMMARY_TIME', "18:00")
    schedule.every().day.at(summary_time).do(send_daily_summary)
    
    # Run scheduler in background thread
    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)
    
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
```

### Custom Alert Rules

Add custom logic in `classify_image()`:

```python
# Alert only during business hours
import datetime
current_hour = datetime.datetime.now().hour
if 9 <= current_hour <= 17:
    # Send notification
    send_notification_sync(...)

# Alert on specific confidence thresholds
if result['confidence'] > 0.9 and result['stage'] >= 2:
    # High confidence spoilage detected
    send_notification_sync(...)
```

---

## Security Best Practices

1. **Never commit tokens:** Always use environment variables
2. **Use `.env` files:** Add `.env` to `.gitignore`
3. **Rotate tokens regularly:** Generate new tokens monthly via BotFather
4. **Restrict bot permissions:** Your bot only needs to send messages
5. **Monitor usage:** Check for unauthorized access in BotFather

---

## Complete Example

Here's a minimal working example combining all steps:

```python
# At top of server.py
import asyncio
from notification_system import init_notifier, send_notification_sync
notifier = None

# In classify_image(), after prediction_history.appendleft(result)
if notifier and notifier.enabled and result.get('stage', 0) in [2, 3]:
    try:
        send_notification_sync(notifier, notifier.send_freshness_alert(
            stage=result['stage'],
            stage_name=result['stage_name'],
            confidence=result['confidence'],
            filename=result['filename'],
            image_path=image_path,
            stage_probabilities=result.get('stage_probabilities'),
            hex_colors=result.get('hex_colors')
        ))
    except Exception as e:
        print(f\"Notification error: {e}\")

# In main(), after load_model()
global notifier
notifier = init_notifier()
if notifier and notifier.enabled:
    print("✅ Telegram notifications enabled")

# At module level
if getattr(config, 'TELEGRAM_ENABLED', True):
    notifier = init_notifier()
```

---

## Support

For more information:
- [Setup Guide](setup_telegram.md) - Bot creation instructions
- [Architecture Documentation](ARCHITECTURE.md) - System design
- [Telegram Bot API](https://core.telegram.org/bots/api) - Official API docs

If you encounter issues, check the server logs and verify your configuration with the `/api/notification-status` endpoint.
