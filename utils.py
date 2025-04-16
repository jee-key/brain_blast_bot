import asyncio
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
import re
import logging
import os

# Global variable shared with bot.py
user_sessions = {}

# Get environment variable for hints
ENABLE_HINTS = os.getenv("ENABLE_HINTS", "true").lower() == "true"

# Add normalize_answer function for consistent answer processing
def normalize_answer(text):
    """
    Normalizes answer text for more accurate comparison.
    - Removes punctuation, extra spaces, quotes
    - Removes content in parentheses and brackets
    - Converts to lowercase
    """
    if not text:
        return ""
        
    # Convert to lowercase
    text = text.lower()
    
    # Remove content in parentheses and brackets
    text = re.sub(r'\(.+?\)', '', text)  # Remove content in parentheses
    text = re.sub(r'\[.+?\]', '', text)  # Remove content in square brackets
    
    # Remove quotes and special characters
    text = re.sub(r'[\"«»„""]', '', text)  # Remove various quotes
    text = re.sub(r'[.,;:!?]', '', text)  # Remove punctuation
    
    # Replace multiple spaces with a single space and trim
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

async def start_timer(chat_id, context, user_id, q, mode):
    global user_sessions

    MODE_CONFIG = {
        "normal": {"total_time": 60, "hint_time": 30, "show_hint": True},
        "speed": {"total_time": 30, "hint_time": 0, "show_hint": False},
        "no_hints": {"total_time": 50, "hint_time": 0, "show_hint": False}
    }

    config = MODE_CONFIG.get(mode, MODE_CONFIG["normal"])

    if user_id not in user_sessions:
        logging.warning(f"[timer] Error: no session for user {user_id}")
        return

    # Store the current task so it can be cancelled if needed
    current_task = asyncio.current_task()
    user_sessions[user_id]["timer_task"] = current_task
    
    # *** CRITICAL FIX: Add a flag to track if timer expired ***
    user_sessions[user_id]["timer_expired"] = False

    try:
        # Calculate reading time based on question length and images
        question_text = q.get("question", "")
        words_count = len(question_text.split())
        reading_time = min(20, max(5, (words_count // 15) * 5))

        if q.get("image_urls"):
            reading_time += 5 * len(q.get("image_urls", []))

        # First check - if user already answered
        if user_id not in user_sessions:
            logging.info(f"[timer] User session {user_id} removed before timer started")
            return

        session = user_sessions.get(user_id, {})
        if session.get("answered") or session.get("correct_answer"):
            logging.info(f"[timer] User {user_id} already answered, skipping timer")
            return

        # Send reading time message
        if reading_time > 5 and mode != "blind":
            try:
                await context.bot.send_message(
                    chat_id, 
                    f"⏳ У вас есть {reading_time} секунд на чтение вопроса."
                )
                logging.info(f"[timer] Sent reading time message to user {user_id}")
            except Exception as e:
                logging.error(f"[timer] Error sending reading time message: {e}")

        # Wait for reading time with periodic checks
        for _ in range(reading_time):
            await asyncio.sleep(1)
            # Check if session still exists or user answered
            if user_id not in user_sessions:
                logging.info(f"[timer] User session {user_id} removed during reading time")
                return
                
            session = user_sessions.get(user_id, {})
            if session.get("answered") or session.get("correct_answer"):
                logging.info(f"[timer] User {user_id} answered during reading time")
                return

        # Another check before sending timer start message
        if user_id not in user_sessions:
            return
            
        session = user_sessions.get(user_id, {})
        if session.get("answered") or session.get("correct_answer"):
            return

        # Send timer start message
        if mode != "blind":
            try:
                await context.bot.send_message(
                    chat_id, 
                    f"⏱️ Отсчет времени начался! ({config['total_time']} секунд)"
                )
                logging.info(f"[timer] Started countdown for user {user_id}")
            except Exception as e:
                logging.error(f"[timer] Error sending timer start message: {e}")

        # Wait for answering time, checking each second if user has answered
        for i in range(config['total_time']):
            await asyncio.sleep(1)
            
            # Check if session still exists or user answered
            if user_id not in user_sessions:
                logging.info(f"[timer] User session {user_id} removed during answer time")
                return
                
            session = user_sessions.get(user_id, {})
            # *** CRITICAL FIX: Add check for timer_expired flag ***
            if session.get("timer_expired"):
                logging.info(f"[timer] Timer already expired and handled for user {user_id}")
                return
                
            if session.get("answered") or session.get("correct_answer"):
                logging.info(f"[timer] User {user_id} answered during answer time, stopping timer")
                return
                
            # Send hint at halfway point if enabled for this mode
            if config["show_hint"] and i == config["hint_time"] and ENABLE_HINTS:
                # Just to be extra safe, check one more time right before sending
                if user_id not in user_sessions:
                    return
                    
                session = user_sessions.get(user_id, {})
                if session.get("answered") or session.get("correct_answer"):
                    return
                
                answer = q.get("answer", "")
                hint = format_hint(answer)
                
                try:
                    await context.bot.send_message(
                        chat_id, 
                        f"💡 Подсказка: {hint}"
                    )
                    logging.info(f"[timer] Sent hint to user {user_id}")
                except Exception as e:
                    logging.error(f"[timer] Error sending hint: {e}")

        # Final check before time's up message
        if user_id not in user_sessions:
            return
            
        session = user_sessions.get(user_id, {})
        if session.get("answered") or session.get("correct_answer"):
            logging.info(f"[timer] User {user_id} answered at the last moment")
            return
            
        # *** CRITICAL FIX: Set timer_expired flag first ***
        if user_id in user_sessions:
            user_sessions[user_id]["timer_expired"] = True
            logging.info(f"[timer] Setting timer_expired flag for user {user_id}")

        # Mark as answered to prevent duplicate answers
        if user_id in user_sessions:
            user_sessions[user_id]["answered"] = True
            logging.info(f"[timer] Time's up for user {user_id}, marking as answered")

        # Send time's up message with answer reveal button
        keyboard = [[InlineKeyboardButton("👀 Показать ответ", callback_data=f"reveal_answer:{user_id}")]]
        try:
            await context.bot.send_message(
                chat_id, 
                "⏰ Время вышло!", 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            logging.info(f"[timer] Sent time's up message to user {user_id}")
        except Exception as e:
            logging.error(f"[timer] Error sending time's up message: {e}")
    except asyncio.CancelledError:
        logging.info(f"[timer] Timer for user {user_id} was cancelled (answer received)")
        return
    except Exception as e:
        logging.error(f"[timer] Timer error: {e}", exc_info=True)

def format_hint(answer: str) -> str:
    """
    Creates a subtle hint based on the answer.
    Designed to give just enough information without revealing too much.
    """
    if not answer:
        return "Подсказка недоступна."
    
    # Clean the answer from brackets and extra annotations
    clean_answer = re.sub(r'\(.+?\)', '', answer)  # Remove content in parentheses
    clean_answer = re.sub(r'\[.+?\]', '', clean_answer)  # Remove content in square brackets
    clean_answer = re.sub(r'[\"«»]', '', clean_answer)  # Remove quotes
    clean_answer = re.sub(r'\s+', ' ', clean_answer).strip()  # Normalize whitespace
    
    # If answer is empty after cleaning, use the original
    if not clean_answer:
        clean_answer = answer
    
    words = clean_answer.split()
    total_chars = len(''.join(words))
    
    # Different hint strategies based on answer type
    if len(words) > 1:  # Multi-word answer
        # Count total letters (excluding spaces)
        
        # Just give number of words and total letters
        return f"Ответ содержит {len(words)} слов ({total_chars} букв)"
        
    else:  # Single-word answer
        word = words[0]
        
        if len(word) <= 3:  # Very short word
            return f"Ответ - короткое слово из {len(word)} букв"
        
        # For longer words, just give length and first letter
        first_letter = word[0].upper()
        return f"Ответ - слово из {len(word)} букв, начинается на '{first_letter}'"
