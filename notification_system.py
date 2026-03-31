"""
Telegram Notification System for Freshness Monitor
===================================================
Provides real-time alerts for freshness stage changes, daily summaries,
and system health monitoring via Telegram bot.

Features:
  - Async notification dispatch (non-blocking)
  - Retry logic with exponential backoff
  - Multiple notification types (alerts, summaries, health checks)
  - Rich formatting with emojis and stage-colored indicators
  - Error handling and logging

Usage:
  from notification_system import TelegramNotifier
  
  notifier = TelegramNotifier(bot_token, chat_id)
  await notifier.send_freshness_alert(stage, confidence, image_path)
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Optional, Dict, List
from io import BytesIO

try:
    from telegram import Bot, InputFile
    from telegram.error import TelegramError, RetryAfter, TimedOut
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logging.warning("python-telegram-bot not installed. Telegram notifications disabled.")

import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Handles all Telegram notifications for the Freshness Monitor system.
    Supports async operations to prevent blocking the main server.
    """
    
    # Stage emojis for visual appeal
    STAGE_EMOJIS = {
        0: "🟢",  # Very Fresh
        1: "🟡",  # Fresh
        2: "🟠",  # Early Spoilage
        3: "🔴",  # Spoiled
    }
    
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """
        Initialize the Telegram notifier.
        
        Args:
            bot_token: Telegram bot token (optional, falls back to config)
            chat_id: Telegram chat ID (optional, falls back to config)
        """
        if not TELEGRAM_AVAILABLE:
            logger.error("Telegram bot library not available. Install with: pip install python-telegram-bot")
            self.enabled = False
            return
        
        self.bot_token = bot_token or getattr(config, 'TELEGRAM_BOT_TOKEN', None)
        self.chat_id = chat_id or getattr(config, 'TELEGRAM_CHAT_ID', None)
        
        # Validate configuration
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot token or chat ID not configured. Notifications disabled.")
            self.enabled = False
            return
        
        # Initialize bot
        try:
            self.bot = Bot(token=self.bot_token)
            self.enabled = True
            logger.info("Telegram notifier initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")
            self.enabled = False
    
    async def _send_message_with_retry(
        self, 
        text: str, 
        max_retries: int = 3,
        parse_mode: str = "HTML"
    ) -> bool:
        """
        Send a message with retry logic and exponential backoff.
        
        Args:
            text: Message text to send
            max_retries: Maximum number of retry attempts
            parse_mode: Message formatting (HTML or Markdown)
        
        Returns:
            True if message sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("Telegram notifications disabled, skipping message")
            return False
        
        for attempt in range(max_retries):
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=parse_mode
                )
                logger.info("Telegram message sent successfully")
                return True
            
            except RetryAfter as e:
                wait_time = e.retry_after
                logger.warning(f"Rate limited. Retrying after {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            
            except TimedOut:
                wait_time = 2 ** attempt  # Exponential backoff
                logger.warning(f"Request timed out. Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            
            except TelegramError as e:
                logger.error(f"Telegram API error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return False
            
            except Exception as e:
                logger.error(f"Unexpected error sending Telegram message: {e}")
                return False
        
        return False
    
    async def _send_photo_with_retry(
        self,
        photo_path: str,
        caption: str,
        max_retries: int = 3,
        parse_mode: str = "HTML"
    ) -> bool:
        """
        Send a photo with caption with retry logic.
        
        Args:
            photo_path: Path to the image file
            caption: Photo caption text
            max_retries: Maximum number of retry attempts
            parse_mode: Caption formatting
        
        Returns:
            True if photo sent successfully, False otherwise
        """
        if not self.enabled:
            return False
        
        if not os.path.exists(photo_path):
            logger.error(f"Image file not found: {photo_path}")
            return False
        
        for attempt in range(max_retries):
            try:
                with open(photo_path, 'rb') as photo_file:
                    await self.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=InputFile(photo_file),
                        caption=caption,
                        parse_mode=parse_mode
                    )
                logger.info("Telegram photo sent successfully")
                return True
            
            except RetryAfter as e:
                wait_time = e.retry_after
                logger.warning(f"Rate limited. Retrying after {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            
            except TimedOut:
                wait_time = 2 ** attempt
                logger.warning(f"Request timed out. Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            
            except TelegramError as e:
                logger.error(f"Telegram API error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return False
            
            except Exception as e:
                logger.error(f"Unexpected error sending Telegram photo: {e}")
                return False
        
        return False
    
    async def send_freshness_alert(
        self,
        stage: int,
        stage_name: str,
        confidence: float,
        filename: str,
        image_path: Optional[str] = None,
        stage_probabilities: Optional[Dict] = None,
        hex_colors: Optional[Dict] = None
    ) -> bool:
        """
        Send a freshness stage alert with detailed information.
        
        Args:
            stage: Freshness stage number (0-3)
            stage_name: Human-readable stage name
            confidence: Prediction confidence (0-1)
            filename: Name of the analyzed file
            image_path: Optional path to the image file
            stage_probabilities: Optional dict of stage probabilities
            hex_colors: Optional dict of dominant hex colors
        
        Returns:
            True if alert sent successfully
        """
        if not self.enabled:
            return False
        
        emoji = self.STAGE_EMOJIS.get(stage, "⚪")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Build message with rich formatting
        message = f"{emoji} <b>Freshness Alert</b> {emoji}\n\n"
        message += f"<b>Stage:</b> {stage_name}\n"
        message += f"<b>Confidence:</b> {confidence * 100:.1f}%\n"
        message += f"<b>File:</b> {filename}\n"
        message += f"<b>Time:</b> {timestamp}\n"
        
        # Add stage probabilities if available
        if stage_probabilities:
            message += "\n<b>Stage Probabilities:</b>\n"
            for stage_label, prob in stage_probabilities.items():
                emoji_icon = self.STAGE_EMOJIS.get(
                    list(config.LABEL_NAMES.values()).index(stage_label)
                    if stage_label in config.LABEL_NAMES.values() else 0,
                    "⚪"
                )
                message += f"{emoji_icon} {stage_label}: {prob * 100:.1f}%\n"
        
        # Add dominant colors if available
        if hex_colors:
            message += f"\n<b>Dominant Colors:</b> "
            message += ", ".join(hex_colors.values())
        
        # Add action recommendations based on stage
        if stage >= 2:  # Early Spoilage or worse
            message += f"\n\n⚠️ <b>Action Required:</b> "
            if stage == 2:
                message += "Product is entering early spoilage stage. Consider consumption soon."
            elif stage == 3:
                message += "Product has spoiled. Do not consume!"
        
        # Send photo if available and stage is concerning
        if image_path and stage >= getattr(config, 'TELEGRAM_PHOTO_MIN_STAGE', 2):
            caption = f"{emoji} {stage_name} ({confidence * 100:.1f}% confidence)"
            success = await self._send_photo_with_retry(image_path, caption)
            if success:
                # Send detailed message separately
                return await self._send_message_with_retry(message)
            else:
                # Fallback to text-only if photo fails
                return await self._send_message_with_retry(message)
        else:
            # Send text-only message
            return await self._send_message_with_retry(message)
    
    async def send_daily_summary(self, predictions: List[Dict]) -> bool:
        """
        Send a daily summary of all predictions.
        
        Args:
            predictions: List of prediction dictionaries
        
        Returns:
            True if summary sent successfully
        """
        if not self.enabled or not predictions:
            return False
        
        timestamp = datetime.now().strftime("%Y-%m-%d")
        
        # Count predictions by stage
        stage_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        for pred in predictions:
            stage = pred.get('stage', 0)
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        
        # Build summary message
        message = f"📊 <b>Daily Summary - {timestamp}</b>\n\n"
        message += f"<b>Total Predictions:</b> {len(predictions)}\n\n"
        
        message += "<b>Breakdown by Stage:</b>\n"
        for stage, count in sorted(stage_counts.items()):
            if count > 0:
                emoji = self.STAGE_EMOJIS.get(stage, "⚪")
                stage_name = config.LABEL_NAMES.get(stage, f"Stage {stage}")
                percentage = (count / len(predictions)) * 100
                message += f"{emoji} {stage_name}: {count} ({percentage:.1f}%)\n"
        
        # Add warnings if needed
        warning_count = stage_counts.get(2, 0) + stage_counts.get(3, 0)
        if warning_count > 0:
            message += f"\n⚠️ <b>{warning_count} items require attention!</b>"
        else:
            message += f"\n✅ <b>All items are fresh!</b>"
        
        return await self._send_message_with_retry(message)
    
    async def send_system_health(
        self,
        status: str,
        uptime_seconds: int,
        total_predictions: int,
        model_loaded: bool,
        last_prediction: Optional[Dict] = None
    ) -> bool:
        """
        Send system health status notification.
        
        Args:
            status: System status (running, warning, error)
            uptime_seconds: Server uptime in seconds
            total_predictions: Total number of predictions made
            model_loaded: Whether ML model is loaded
            last_prediction: Last prediction data (optional)
        
        Returns:
            True if health report sent successfully
        """
        if not self.enabled:
            return False
        
        # Calculate uptime
        uptime_hours = uptime_seconds // 3600
        uptime_mins = (uptime_seconds % 3600) // 60
        
        # Status emoji
        status_emoji = {
            'running': '✅',
            'warning': '⚠️',
            'error': '❌',
            'starting': '🔄'
        }.get(status.lower(), '❓')
        
        # Build health message
        message = f"{status_emoji} <b>System Health Report</b>\n\n"
        message += f"<b>Status:</b> {status.upper()}\n"
        message += f"<b>Uptime:</b> {uptime_hours}h {uptime_mins}m\n"
        message += f"<b>Total Predictions:</b> {total_predictions}\n"
        message += f"<b>Model Status:</b> {'✅ Loaded' if model_loaded else '❌ Not Loaded'}\n"
        
        if last_prediction:
            stage_name = last_prediction.get('stage_name', 'Unknown')
            timestamp = last_prediction.get('timestamp', 'N/A')
            emoji = self.STAGE_EMOJIS.get(last_prediction.get('stage', 0), "⚪")
            message += f"\n<b>Last Prediction:</b>\n"
            message += f"{emoji} {stage_name}\n"
            message += f"Time: {timestamp}\n"
        
        return await self._send_message_with_retry(message)
    
    async def send_test_notification(self) -> bool:
        """
        Send a test notification to verify configuration.
        
        Returns:
            True if test message sent successfully
        """
        if not self.enabled:
            return False
        
        message = "🧪 <b>Test Notification</b>\n\n"
        message += "Freshness Monitor Telegram notifications are working correctly!\n\n"
        message += f"<b>Bot Token:</b> {self.bot_token[:10]}...\n"
        message += f"<b>Chat ID:</b> {self.chat_id}\n"
        message += f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return await self._send_message_with_retry(message)
    
    async def send_error_alert(self, error_type: str, error_message: str) -> bool:
        """
        Send an error alert notification.
        
        Args:
            error_type: Type of error (e.g., "Model Loading", "Prediction")
            error_message: Detailed error message
        
        Returns:
            True if error alert sent successfully
        """
        if not self.enabled:
            return False
        
        message = f"❌ <b>System Error Alert</b>\n\n"
        message += f"<b>Error Type:</b> {error_type}\n"
        message += f"<b>Message:</b> {error_message}\n"
        message += f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        message += "⚠️ Please check the system logs for more details."
        
        return await self._send_message_with_retry(message)


# Convenience function for sync contexts
def send_notification_sync(notifier: TelegramNotifier, coro):
    """
    Helper function to send notifications from synchronous code.
    Creates a new event loop if needed.
    
    Args:
        notifier: TelegramNotifier instance
        coro: Coroutine to execute
    
    Returns:
        Result of the coroutine
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is already running, schedule the task
            asyncio.create_task(coro)
            return True
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop exists, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# Global notifier instance (initialized in server.py)
_global_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> Optional[TelegramNotifier]:
    """Get the global notifier instance."""
    return _global_notifier


def init_notifier(bot_token: Optional[str] = None, chat_id: Optional[str] = None):
    """Initialize the global notifier instance."""
    global _global_notifier
    _global_notifier = TelegramNotifier(bot_token, chat_id)
    return _global_notifier
