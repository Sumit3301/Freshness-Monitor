# Telegram Notification Setup Guide

This guide walks you through setting up Telegram notifications for the Freshness Monitor system.

## 📱 Overview

The Telegram notification system provides real-time alerts for:
- **Freshness Warnings**: Notifications when products reach Stage 3 (Early Spoilage) or Stage 4 (Spoiled)
- **Daily Summaries**: End-of-day reports with statistics
- **System Health**: Periodic health checks and startup notifications
- **Error Alerts**: Critical system errors

---

## 🤖 Step 1: Create a Telegram Bot

1. **Open Telegram** and search for `@BotFather` (the official bot creation tool)

2. **Start a conversation** with BotFather by clicking "Start" or sending `/start`

3. **Create a new bot** by sending the command:
   ```
   /newbot
   ```

4. **Choose a name** for your bot (this is the display name users will see):
   ```
   Example: Freshness Monitor Bot
   ```

5. **Choose a username** for your bot (must end in 'bot'):
   ```
   Example: freshness_monitor_bot
   ```

6. **Save your bot token** - BotFather will send you a message like:
   ```
   Done! Congratulations on your new bot. You will find it at t.me/freshness_monitor_bot. 
   You can now add a description...
   
   Use this token to access the HTTP API:
   1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890
   
   Keep your token secure and store it safely, it can be used by anyone to control your bot.
   ```

   ⚠️ **Keep this token secure!** Anyone with this token can control your bot.

---

## 👤 Step 2: Get Your Chat ID

You need your Chat ID to receive messages from the bot.

### Option A: Using a Bot (Easiest)

1. **Search for** `@userinfobot` or `@get_id_bot` in Telegram

2. **Start the bot** and it will immediately send you your Chat ID:
   ```
   Your Chat ID: 123456789
   ```

### Option B: Manual Method

1. **Send a message** to your newly created bot (search for it using the username from Step 1)

2. **Visit this URL** in your browser (replace `YOUR_BOT_TOKEN` with your actual token):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```

3. **Look for the Chat ID** in the JSON response:
   ```json
   {
     "ok": true,
     "result": [{
       "message": {
         "chat": {
           "id": 123456789,
           ...
         }
       }
     }]
   }
   ```

### For Group Notifications

If you want notifications in a Telegram group:

1. **Create a group** in Telegram
2. **Add your bot** to the group as a member
3. **Send a message** in the group (mention the bot with `@your_bot_username`)
4. **Get the group Chat ID** using Option B above (group IDs are negative numbers like `-123456789`)

---

## ⚙️ Step 3: Configure the Freshness Monitor

### Method 1: Environment Variables (Recommended for Production)

Set environment variables on your system:

**Linux/Mac:**
```bash
export TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
export TELEGRAM_CHAT_ID="123456789"
```

**Windows (Command Prompt):**
```cmd
set TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890
set TELEGRAM_CHAT_ID=123456789
```

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
$env:TELEGRAM_CHAT_ID="123456789"
```

**For Render.com or other cloud platforms:**
- Go to your service's environment variables settings
- Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as environment variables

### Method 2: Direct Configuration (Development Only)

⚠️ **Not recommended for production** - tokens will be visible in your code!

Edit `config.py` and replace the values:
```python
TELEGRAM_BOT_TOKEN = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890"
TELEGRAM_CHAT_ID = "123456789"
```

---

## 📦 Step 4: Install Dependencies

Install the required Python package:

```bash
pip install python-telegram-bot==20.7
```

Or install all dependencies:
```bash
pip install -r requirements.txt
```

---

## 🧪 Step 5: Test Your Configuration

### Using the API Endpoint

Start your server:
```bash
python server.py
```

Then send a test request:
```bash
curl -X POST http://localhost:5000/api/test-notification
```

You should receive a test message in Telegram!

### Using Python Directly

Create a test script `test_telegram.py`:
```python
import asyncio
from notification_system import TelegramNotifier

async def test():
    notifier = TelegramNotifier()
    success = await notifier.send_test_notification()
    print(f"Test notification sent: {success}")

asyncio.run(test())
```

Run it:
```bash
python test_telegram.py
```

---

## 🎛️ Step 6: Customize Notification Preferences

Edit `config.py` to adjust notification settings:

```python
# Which stages trigger alerts? (0=Very Fresh, 1=Fresh, 2=Early Spoilage, 3=Spoiled)
TELEGRAM_ALERT_ON_STAGES = [2, 3]  # Only alert on concerning stages

# Send photos with notifications? (uses more bandwidth)
TELEGRAM_SEND_PHOTO = True

# Minimum stage to include photos (saves bandwidth for fresh products)
TELEGRAM_PHOTO_MIN_STAGE = 2

# Daily summary report
TELEGRAM_DAILY_SUMMARY_ENABLED = True
TELEGRAM_DAILY_SUMMARY_TIME = "18:00"  # 6 PM daily summary

# Health checks every hour
TELEGRAM_HEALTH_CHECK_INTERVAL = 3600  # seconds

# Notify when server starts
TELEGRAM_NOTIFY_ON_STARTUP = True
```

---

## 📱 Step 7: Bot Commands (Optional)

You can add custom commands to your bot via BotFather:

1. Send `/setcommands` to BotFather
2. Select your bot
3. Add these commands:
   ```
   start - Start receiving notifications
   status - Get system status
   summary - Get today's summary
   help - Show help message
   ```

Then implement these commands in your bot (see Advanced Features below).

---

## 🔒 Security Best Practices

1. **Never commit tokens to Git**
   - Always use environment variables
   - Add `.env` files to `.gitignore`

2. **Restrict bot permissions**
   - Your bot only needs to send messages
   - Disable unused privacy settings in BotFather

3. **Use group IDs carefully**
   - Only add your bot to trusted groups
   - Monitor group members to prevent unauthorized access

4. **Rotate tokens periodically**
   - Use `/token` command in BotFather to generate new tokens
   - Update your configuration when rotating

---

## 🐛 Troubleshooting

### "Bot token not configured" Error

**Problem:** Server starts but notifications don't work.

**Solution:** 
- Check that environment variables are set correctly
- Verify the token format (should be like `1234567890:ABC...`)
- Make sure you're using the correct token from BotFather

### "Chat not found" Error

**Problem:** Bot sends messages but you don't receive them.

**Solution:**
- Verify your Chat ID is correct
- Make sure you've started a conversation with the bot
- For groups, ensure the bot is a member and has permission to send messages

### Messages Not Appearing

**Problem:** No errors but messages don't arrive.

**Solution:**
- Check if bot is blocked in Telegram
- Verify internet connectivity on the server
- Check server logs for rate limiting issues
- Try the test notification endpoint

### Import Error: "No module named 'telegram'"

**Problem:** Python can't find the telegram library.

**Solution:**
```bash
pip install python-telegram-bot==20.7
```

### Async Warnings in Logs

**Problem:** Warnings about event loops.

**Solution:** This is normal in some environments. Notifications will still work, but you can suppress warnings by upgrading to Python 3.9+.

---

## 🚀 Advanced Features

### Multiple Chat IDs

To send notifications to multiple users/groups, modify `config.py`:

```python
TELEGRAM_CHAT_ID = "123456789,987654321,-111222333"  # Comma-separated
```

Then update `notification_system.py` to loop through all IDs.

### Custom Alert Rules

Create custom notification rules in `server.py`:

```python
# Alert only during business hours
import datetime
current_hour = datetime.datetime.now().hour
if 9 <= current_hour <= 17 and stage >= 2:
    send_notification_sync(notifier, notifier.send_freshness_alert(...))
```

### Scheduled Reports

Use `schedule` library for timed reports:

```bash
pip install schedule
```

```python
import schedule
import time

def send_daily_report():
    # Send summary notification
    pass

schedule.every().day.at("18:00").do(send_daily_report)

while True:
    schedule.run_pending()
    time.sleep(60)
```

---

## 📊 Notification Examples

### Freshness Alert

```
🟠 Freshness Alert 🟠

Stage: Stage 3 - Early Spoilage
Confidence: 87.3%
File: sample_12h.jpg
Time: 2026-03-04 16:45:22

Stage Probabilities:
🟢 Stage 1 - Very Fresh: 2.1%
🟡 Stage 2 - Fresh: 8.4%
🟠 Stage 3 - Early Spoilage: 87.3%
🔴 Stage 4 - Spoiled: 2.2%

Dominant Colors: #c5c7c1, #7d7764, #a4a598

⚠️ Action Required: Product is entering early spoilage stage. Consider consumption soon.
```

### Daily Summary

```
📊 Daily Summary - 2026-03-04

Total Predictions: 24

Breakdown by Stage:
🟢 Stage 1 - Very Fresh: 8 (33.3%)
🟡 Stage 2 - Fresh: 10 (41.7%)
🟠 Stage 3 - Early Spoilage: 5 (20.8%)
🔴 Stage 4 - Spoiled: 1 (4.2%)

⚠️ 6 items require attention!
```

### System Health

```
✅ System Health Report

Status: RUNNING
Uptime: 12h 34m
Total Predictions: 156
Model Status: ✅ Loaded

Last Prediction:
🟢 Stage 1 - Very Fresh
Time: 2026-03-04T16:45:22
```

---

## 📚 Additional Resources

- [Telegram Bot API Documentation](https://core.telegram.org/bots/api)
- [python-telegram-bot Library](https://github.com/python-telegram-bot/python-telegram-bot)
- [BotFather Commands Reference](https://core.telegram.org/bots/features#botfather)

---

## 💬 Support

If you encounter issues:
1. Check the troubleshooting section above
2. Review server logs for error messages
3. Test with the `/api/test-notification` endpoint
4. Verify your bot token and chat ID are correct

For questions about the Freshness Monitor system, see the main [README.md](README.md).
