import asyncio
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
import re
import logging

# Global variable shared with bot.py
user_sessions = {}

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

    try:
        # Calculate reading time based on question length and images
        question_text = q.get("question", "")
        words_count = len(question_text.split())
        reading_time = min(20, max(5, (words_count // 15) * 5))

        if q.get("image_urls"):
            reading_time += 5 * len(q.get("image_urls", []))

        # Check if the user has already answered before sending reading time message
        session = user_sessions.get(user_id, {})
        if session.get("answered") or session.get("correct_answer"):
            logging.info(f"[timer] User {user_id} already answered, skipping timer")
            return

        # Send reading time message
        if reading_time > 5 and mode != "blind":
            await context.bot.send_message(
                chat_id, 
                f"⏳ У вас есть {reading_time} секунд на чтение вопроса."
            )

        # Wait for reading time
        await asyncio.sleep(reading_time)

        # Check again if user answered during reading time
        session = user_sessions.get(user_id, {})
        if not session or session.get("answered") or session.get("correct_answer"):
            logging.info(f"[timer] User {user_id} answered during reading time")
            return

        # Send timer start message
        if mode != "blind":
            await context.bot.send_message(
                chat_id, 
                f"⏱️ Отсчет времени начался! ({config['total_time']} секунд)"
            )

        # Wait for answering time, checking each second if user has answered
        for i in range(config['total_time']):
            await asyncio.sleep(1)
            session = user_sessions.get(user_id, {})
            
            # Skip if the session was removed or user answered
            if not session or session.get("answered") or session.get("correct_answer"):
                logging.info(f"[timer] User {user_id} answered or session removed")
                return
                
            # Send hint at halfway point if enabled for this mode
            if config["show_hint"] and i == config["hint_time"] and ENABLE_HINTS:
                answer = q.get("answer", "")
                hint = format_hint(answer)
                
                # Check once more if user answered before sending hint
                session = user_sessions.get(user_id, {})
                if not session or session.get("answered") or session.get("correct_answer"):
                    return
                    
                await context.bot.send_message(chat_id, f"💡 Подсказка: {hint}")

        # Final check before time's up message
        session = user_sessions.get(user_id, {})
        if not session or session.get("answered") or session.get("correct_answer"):
            logging.info(f"[timer] User {user_id} answered at the last moment")
            return

        # Mark as answered to prevent duplicate answers
        if user_id in user_sessions:
            user_sessions[user_id]["answered"] = True
            logging.info(f"[timer] Time's up for user {user_id}, marking as answered")

        # Send time's up message with answer reveal button
        keyboard = [[InlineKeyboardButton("👀 Показать ответ", callback_data=f"reveal_answer:{user_id}")]]
        await context.bot.send_message(
            chat_id, 
            "⏰ Время вышло!", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
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
