import asyncio
import logging
import time
import datetime
import re
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Global user session storage
user_sessions = {}

# Default timer settings
DEFAULT_TIMER = 60  # seconds

def normalize_answer(text):
    """Normalizes an answer for comparison"""
    if not text:
        return ""
    
    # Convert to lowercase
    text = text.lower()
    
    # Remove all punctuation except hyphens and spaces
    text = re.sub(r'[^\w\s-]', '', text)
    
    # Replace multiple spaces with a single space
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Remove unnecessary articles and pronouns that don't affect meaning
    stop_words = [' и ', ' или ', ' a ', ' an ', ' the ']
    for word in stop_words:
        text = text.replace(word, ' ')
    
    # Normalize some Russian letters that might be mistyped
    replacements = {
        'ё': 'е',
        'й': 'и',
        'ъ': 'ь',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    return text.strip()

def format_hint(answer, revealed_percentage):
    """
    Format a hint that shows only the first letter of each word and masks the rest
    """
    if not answer:
        return "Ошибка: нет ответа для подсказки"
    
    # Split the answer into words
    words = answer.split()
    hint = []
    
    # Create a hint for each word
    for word in words:
        if len(word) > 0:
            # Show the first letter and mask the rest
            masked_word = word[0] + "•" * (len(word) - 1)
            hint.append(masked_word)
    
    # Join the words back with spaces
    return " ".join(hint)

async def start_timer(chat_id, context, user_id, question_data, mode="normal"):
    """
    Start a timer for a question with proper cleanup.
    
    Args:
        chat_id: The chat ID to send the message to
        context: The context object from the handler
        user_id: The user ID to set the timer for
        question_data: Complete question data object
        mode: Game mode (normal, speed, no_hints)
    """
    # Cancel any existing timer for this user
    if user_id in user_sessions and user_sessions[user_id].get("timer_task"):
        try:
            user_sessions[user_id]["timer_task"].cancel()
            logging.info(f"Cancelled existing timer for user {user_id}")
        except Exception as e:
            logging.error(f"Error cancelling timer: {e}")
    
    # Set up session data
    session = user_sessions.get(user_id, {})
    session.update({
        "q": question_data,
        "mode": mode,
        "answered": False,
        "timer_expired": False,
        "input_processing": False,
        "start_time": time.time(),
        "timer_expired_timestamp": None  # Track exact time when timer expires
    })
    user_sessions[user_id] = session
    
    # Determine timer duration based on mode
    from bot import MODE_TIMES
    timer_duration = MODE_TIMES.get(mode, DEFAULT_TIMER)  # Default: 60 seconds
    
    # Start the timer as a background task
    timer_task = asyncio.create_task(
        _run_timer(chat_id, context, user_id, timer_duration, mode, question_data)
    )
    user_sessions[user_id]["timer_task"] = timer_task
    logging.info(f"Started timer for user {user_id} in mode {mode}: {timer_duration} seconds")

async def _run_timer(chat_id, context, user_id, duration, mode, question_data):
    """
    Run the timer with hints and expiration handling.
    Improved with more precise timing and race condition handling.
    """
    # Extract needed data
    from bot import ENABLE_HINTS
    answer = question_data.get("answer", "")
    
    # Calculate hint times (25%, 50%, 75% of duration)
    show_hints = ENABLE_HINTS and mode != "no_hints"
    
    if show_hints:
        hint_times = [
            duration * 0.5
        ]
    else:
        # No hints for no_hints mode
        hint_times = []
    
    # Start countdown
    elapsed = 0
    interval = 0.5  # Check every half-second for more precise timing
    
    # Pre-calculate and optimize hint messages to reduce delay
    if show_hints:
        hint_messages = [
            f"🕒 Осталось {int(duration * 0.5)} секунд\nПодсказка: {format_hint(answer, 0.5)}"
        ]
    
    try:
        # Record the start time for precise timing
        start_time = time.time()
        
        while elapsed < duration:
            # If answer already processed, exit early
            session = user_sessions.get(user_id, {})
            if session.get("answered", False):
                logging.info(f"Timer stopped early - question already answered by user {user_id}")
                return
            
            # If input is currently being processed, add additional grace time
            # This is critical for last-second answers
            if session.get("input_processing", False):
                logging.info(f"⚠️ [SYNC] Detected input processing during timer check - adding grace period")
                # Continue with loop to give input processing time to complete
                await asyncio.sleep(interval)
                elapsed = time.time() - start_time
                continue
            
            # Precise sleep for each interval
            await asyncio.sleep(interval)
            elapsed = time.time() - start_time
            
            # Check if it's time for a hint
            if show_hints and hint_times and elapsed >= hint_times[0]:
                hint_index = len(hint_times) - len(hint_times)
                try:
                    await context.bot.send_message(
                        chat_id=chat_id, 
                        text=hint_messages[hint_index]
                    )
                except Exception as e:
                    logging.error(f"Failed to send hint: {e}")
                
                hint_times.pop(0)  # Remove this hint time
        
        # Before declaring time's up, check once more if input is being processed
        session = user_sessions.get(user_id, {})
        if session.get("input_processing", False):
            logging.info(f"⚠️ [SYNC] Critical race condition detected - user is processing input exactly as timer expires")
            # Wait a bit longer to let the input processing complete
            await asyncio.sleep(2.0)
            
            # Re-check if user answered during this extended grace period
            session = user_sessions.get(user_id, {})
            if session.get("answered", False):
                logging.info(f"User {user_id} answered during extended input processing grace period")
                return
        
        # Time is up - set timer_expired flag with timestamp for precise timing
        now = datetime.datetime.now()
        timer_expired_timestamp = now.timestamp()
        
        # Add precise timestamp logging for when timer actually expired
        timestamp = now.strftime("%H:%M:%S.%f")[:-3]  # Include milliseconds
        logging.info(f"⏰ TIMER EXPIRED for user {user_id} at {timestamp}")
        
        # Store precise expiration timestamp
        if user_id in user_sessions:
            user_sessions[user_id]["timer_expired"] = True
            user_sessions[user_id]["timer_expired_timestamp"] = timer_expired_timestamp
            logging.info(f"⚠️ Timer expired for user {user_id} at exact time: {timestamp}")
        
        # Add 2.0 second grace period for answers coming in right at timer expiration
        # This is crucial for the "I answered at 22:07:30" issue
        grace_period = 2.0  # 2 seconds grace period
        await asyncio.sleep(grace_period)
        logging.info(f"Grace period of {grace_period} seconds applied for user {user_id}")
        
        # Recheck if user answered during grace period
        session = user_sessions.get(user_id, {})
        if session.get("answered", False) or session.get("input_processing", False):
            logging.info(f"User {user_id} answered during grace period - no timeout message needed")
            return
        
        # No answer during grace period, show time's up message
        try:
            keyboard = [[InlineKeyboardButton("👀 Показать ответ", callback_data=f"reveal_answer:{user_id}")]]
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏰ Время вышло! Нажмите кнопку, чтобы увидеть ответ.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logging.error(f"Failed to send time's up message: {e}")
    
    except asyncio.CancelledError:
        # Timer was cancelled gracefully, just log it
        logging.info(f"Timer cancelled for user {user_id}")
        raise
    except Exception as e:
        # Any other exception in the timer
        logging.error(f"Timer error for user {user_id}: {e}")
