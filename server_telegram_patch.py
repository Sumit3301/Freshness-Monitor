"""
Telegram Integration Patch for server.py
==========================================

Add these code blocks to your server.py file:

1. At the top, after other imports:
"""

# ─── Telegram Notification Import ───────────────────────────────────
import asyncio
from notification_system import init_notifier, send_notification_sync

# Initialize global notifier
notifier = None

"""
2. Modify the classify_image function to add notification trigger.
   Find the classify_image function and add this code after prediction_history.appendleft(result):
"""

        # Telegram notification for concerning stages
        if notifier and notifier.enabled:
            stage = result.get('stage', 0)
            if stage in getattr(config, 'TELEGRAM_ALERT_ON_STAGES', [2, 3]):
                # Send notification asynchronously (non-blocking)
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
                except Exception as e:
                    logger.warning(f"Failed to send Telegram notification: {e}")

"""
3. Add new API endpoint for testing notifications.
   Add this before the main() function:
"""

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


"""
4. Modify the main() function to initialize the notifier.
   Add this code at the beginning of main(), right after load_model():
"""

    # Initialize Telegram notifier
    global notifier
    if getattr(config, 'TELEGRAM_ENABLED', True):
        notifier = init_notifier()
        if notifier and notifier.enabled:
            print("Telegram notifications: ENABLED")
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
                except Exception as e:
                    print(f"Failed to send startup notification: {e}")
        else:
            print("Telegram notifications: DISABLED (check configuration)")
    else:
        print("Telegram notifications: DISABLED (via config)")


"""
5. Also initialize notifier at module level (for gunicorn).
   Add this code after the module-level initialization section:
"""

# Initialize notifier for gunicorn/production
if getattr(config, 'TELEGRAM_ENABLED', True):
    notifier = init_notifier()

"""

===========================================
COMPLETE INTEGRATION INSTRUCTIONS:
===========================================

To integrate Telegram notifications into server.py:

1. Import the notification system at the top:
   ```python
   import asyncio
   from notification_system import init_notifier, send_notification_sync
   notifier = None
   ```

2. In classify_image(), after `prediction_history.appendleft(result)`, add:
   ```python
   # Telegram notification
   if notifier and notifier.enabled and result.get('stage', 0) in getattr(config, 'TELEGRAM_ALERT_ON_STAGES', [2, 3]):
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
           print(f"Telegram notification error: {e}")
   ```

3. Add the test endpoint before main():
   Copy the /api/test-notification and /api/notification-status endpoints from above.

4. In main(), after load_model(), add:
   ```python
   global notifier
   if getattr(config, 'TELEGRAM_ENABLED', True):
       notifier = init_notifier()
       if notifier and notifier.enabled:
           print("Telegram notifications: ENABLED")
       else:
           print("Telegram notifications: DISABLED")
   ```

5. At module level, after server_start_time initialization:
   ```python
   if getattr(config, 'TELEGRAM_ENABLED', True):
       notifier = init_notifier()
   ```

That's it! The system will now send notifications automatically.
"""
