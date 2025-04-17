import asyncio
import logging
import time
import datetime
import re
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

user_sessions = {}

DEFAULT_TIMER = 60  # seconds

def normalize_answer(text):
    if not text:
        return ""
    
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    stop_words = [' и ', ' или ', ' a ', ' an ', ' the ']
    for word in stop_words:
        text = text.replace(word, ' ')
    
    replacements = {
        'ё': 'е',
        'й': 'и',
        'ъ': 'ь',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    return text.strip()

def format_hint(answer, revealed_percentage):
    if not answer:
        return "Ошибка: нет ответа для подсказки"
    
    hint_length = max(1, int(len(answer) * revealed_percentage))
    
    hint = ""
    for i, char in enumerate(answer):
        if i < hint_length:
            hint += char
        elif char == " ":
            hint += " "
        else:
            hint += "•"
    
    return hint

async def start_timer(chat_id, context, user_id, question_data, mode="normal"):
    if user_id in user_sessions and user_sessions[user_id].get("timer_task"):
        try:
            user_sessions[user_id]["timer_task"].cancel()
            logging.info(f"Cancelled existing timer for user {user_id}")
        except Exception as e:
            logging.error(f"Error cancelling timer: {e}")
    
    session = user_sessions.get(user_id, {})
    session.update({
        "q": question_data,
        "mode": mode,
        "answered": False,
        "timer_expired": False,
        "input_processing": False,
        "start_time": time.time(),
        "timer_expired_timestamp": None
    })
    user_sessions[user_id] = session
    
    from bot import MODE_TIMES
    timer_duration = MODE_TIMES.get(mode, DEFAULT_TIMER)
    
    timer_task = asyncio.create_task(
        _run_timer(chat_id, context, user_id, timer_duration, mode, question_data)
    )
    user_sessions[user_id]["timer_task"] = timer_task
    logging.info(f"Started timer for user {user_id} in mode {mode}: {timer_duration} seconds")

async def _run_timer(chat_id, context, user_id, duration, mode, question_data):
    from bot import ENABLE_HINTS
    answer = question_data.get("answer", "")
    
    show_hints = ENABLE_HINTS and mode != "no_hints"
    
    if show_hints:
        hint_times = [
            duration * 0.5
        ]
    else:
        hint_times = []
    
    elapsed = 0
    interval = 0.5
    
    if show_hints:
        hint_messages = [
            f"🕒 Осталось {int(duration * 0.5)} секунд\nПодсказка: {format_hint(answer, 0.5)}"
        ]
    
    try:
        start_time = time.time()
        
        while elapsed < duration:
            session = user_sessions.get(user_id, {})
            if session.get("answered", False):
                logging.info(f"Timer stopped early - question already answered by user {user_id}")
                return
            
            if session.get("input_processing", False):
                logging.info(f"⚠️ [SYNC] Detected input processing during timer check - adding grace period")
                await asyncio.sleep(interval)
                elapsed = time.time() - start_time
                continue
            
            await asyncio.sleep(interval)
            elapsed = time.time() - start_time
            
            if show_hints and hint_times and elapsed >= hint_times[0]:
                hint_index = len(hint_times) - len(hint_times)
                try:
                    await context.bot.send_message(
                        chat_id=chat_id, 
                        text=hint_messages[hint_index]
                    )
                except Exception as e:
                    logging.error(f"Failed to send hint: {e}")
                
                hint_times.pop(0)
        
        session = user_sessions.get(user_id, {})
        if session.get("input_processing", False):
            logging.info(f"⚠️ [SYNC] Critical race condition detected - user is processing input exactly as timer expires")
            await asyncio.sleep(2.0)
            
            session = user_sessions.get(user_id, {})
            if session.get("answered", False):
                logging.info(f"User {user_id} answered during extended input processing grace period")
                return
        
        now = datetime.datetime.now()
        timer_expired_timestamp = now.timestamp()
        
        timestamp = now.strftime("%H:%M:%S.%f")[:-3]
        logging.info(f"⏰ TIMER EXPIRED for user {user_id} at {timestamp}")
        
        if user_id in user_sessions:
            user_sessions[user_id]["timer_expired"] = True
            user_sessions[user_id]["timer_expired_timestamp"] = timer_expired_timestamp
            logging.info(f"⚠️ Timer expired for user {user_id} at exact time: {timestamp}")
        
        grace_period = 2.0
        await asyncio.sleep(grace_period)
        logging.info(f"Grace period of {grace_period} seconds applied for user {user_id}")
        
        session = user_sessions.get(user_id, {})
        if session.get("answered", False) or session.get("input_processing", False):
            logging.info(f"User {user_id} answered during grace period - no timeout message needed")
            return
        
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
        logging.info(f"Timer cancelled for user {user_id}")
        raise
    except Exception as e:
        logging.error(f"Timer error for user {user_id}: {e}")
