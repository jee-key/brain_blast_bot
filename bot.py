import logging
import os
import re 
import asyncio
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
)
from parser import get_random_question
from db import init_db, increment_score, get_top_users
from utils import start_timer, format_hint, user_sessions, normalize_answer
from associations import start_drift_session, add_association, stop_drift_session, drift_sessions

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN provided. Set the BOT_TOKEN environment variable.")

ENABLE_HINTS = os.getenv("ENABLE_HINTS", "true").lower() == "true"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

user_modes = {}

MODES = {
    "normal": "Обычный режим",
    "speed": "На скорость",
    "no_hints": "Без подсказок",
    "drift": "Ассоциативный дрифт"
}

MODE_TIMES = {
    "normal": 60,
    "speed": 30,
    "no_hints": 50
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎮 Выбрать режим", callback_data="choose_mode")],
        [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
        [InlineKeyboardButton("🏆 Рейтинг", callback_data="show_rating")]
    ]
    await update.message.reply_text("Привет! Я Brain Blast 🧠⚡ Бот для тренировки мышления!", reply_markup=InlineKeyboardMarkup(keyboard))


async def choose_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("🧠 Обычный - 60 сек.", callback_data="set_mode:normal")],
        [InlineKeyboardButton("⚡ На скорость - 30 сек.", callback_data="set_mode:speed")],
        [InlineKeyboardButton("🔕 Без подсказок - 50 сек.", callback_data="set_mode:no_hints")],
        [InlineKeyboardButton("🌊 Ассоциативный дрифт (beta)", callback_data="set_mode:drift")]
    ]
    await query.edit_message_text("Выбери режим тренировки:", reply_markup=InlineKeyboardMarkup(keyboard))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    logging.info(f"Button pressed: {query.data} by user {user_id}")
    
    try:
        await query.answer()
    except Exception as e:
        logging.warning(f"Failed to answer callback query: {e}")
    
    if query.data.startswith("set_mode:"):
        mode = query.data.split(":")[1]
        user_id = query.from_user.id
        user_modes[user_id] = mode
        
        if mode == "drift":
            if user_id in drift_sessions:
                del drift_sessions[user_id]
            if user_id in user_sessions:
                del user_sessions[user_id]
                
            start_word = start_drift_session(user_id)
            
            await query.edit_message_text(
                f"✅ Режим установлен: {MODES[mode]}\n\n"
                f"🌊 Ассоциативный дрифт запущен!\n\n"
                f"Я начинаю с: *{start_word}*\n\n"
                f"Напиши свою ассоциацию к этому слову.\n"
                f"Введи /stop чтобы завершить сессию.",
                parse_mode="Markdown"
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("🎮 Выбрать режим", callback_data="choose_mode")],
            [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
            [InlineKeyboardButton("🏆 Рейтинг", callback_data="show_rating")]
        ]
        await query.edit_message_text(
            f"✅ Режим установлен: {MODES[mode]}\n\nЧто хочешь делать дальше?", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data == "choose_mode":
        await choose_mode(update, context)
        return

    if query.data == "new_question":
        user_id = query.from_user.id
        
        if user_modes.get(user_id) == "drift" and user_id in drift_sessions:
            chain = stop_drift_session(user_id)
            logging.info(f"User {user_id} stopped drift session by clicking 'New question' button")
            user_modes[user_id] = "normal"
        
        mode = user_modes.get(user_id, "normal")
        q = get_random_question()

        if not q.get("question") or "ошибка" in q.get("answer", "").lower():
            await query.message.reply_text("⚠️ Ошибка при загрузке вопроса. Попробуйте ещё раз позже.")
            return
        
        user_sessions[user_id] = {"q": q, "mode": mode, "answered": False}
        
        image_urls = q.get("image_urls", [])
        
        question_text = f"❓ Вопрос:\n{q['question']}"
        
        if q.get("metadata_text"):
            question_text += f"\n\n{q['metadata_text']}"
        
        if q.get("question_url"):
            question_text += f"\n\n🔗 [Ссылка на вопрос в базе]({q['question_url']})"
        
        if image_urls:
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
            
            for img_url in image_urls:
                try:
                    await query.message.reply_photo(
                        photo=img_url,
                        caption="📷 Изображение к вопросу"
                    )
                except Exception as e:
                    logging.error(f"Error sending image {img_url}: {e}")
                    await query.message.reply_text(f"⚠️ Не удалось загрузить изображение: {img_url}")
        else:
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
        
        await start_timer(query.message.chat_id, context, user_id, q, mode)

    if query.data == "show_rating":
        top = get_top_users()
        if not top:
            await query.message.reply_text("Рейтинг пока пуст.")
            return
        text = "\n".join([f"{i+1}. {name} — {score}" for i, (name, score) in enumerate(top)])
        await query.message.reply_text(f"🏆 Топ игроков:\n{text}")
        
    if query.data.startswith("reveal_answer:"):
        target_user_id = int(query.data.split(":")[1])
        logging.info(f"Processing reveal_answer for user {target_user_id}, pressed by {user_id}")
        
        session = user_sessions.get(target_user_id, {})
        
        if session and "q" in session:
            answer = session["q"]["answer"]
            comment = session["q"].get("comment") or "Без комментария."
            
            keyboard = [
                [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
                [InlineKeyboardButton("🔄 Продолжить итерацию?", callback_data="continue_iteration")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
            ]
            
            if session.get("timer_task") and not session.get("timer_task").done():
                try:
                    session["timer_task"].cancel()
                    session["timer_task"] = None
                    logging.info(f"Timer cancelled for user {target_user_id} when showing answer")
                except Exception as e:
                    logging.error(f"Failed to cancel timer: {e}")
            
            user_sessions[target_user_id]["answered"] = True
            
            try:
                message_text = f"📝 Ответ: {answer}\n💬 {comment}"
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text, 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                logging.info(f"✅ Successfully sent answer reveal message to chat {chat_id}")
            except Exception as e:
                logging.error(f"❌ Error sending reveal answer message: {e}")
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"📝 Ответ: {answer}"
                    )
                except Exception as e2:
                    logging.error(f"Failed to send even simple answer reveal: {e2}")
        else:
            logging.warning(f"Session not found for user {target_user_id} in reveal_answer")
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Не удалось найти информацию о вопросе. Попробуйте запросить новый вопрос."
                )
            except Exception as e:
                logging.error(f"Failed to send session not found message: {e}")
        return

    if query.data == "continue_iteration":
        user_id = query.from_user.id
        mode = user_modes.get(user_id, "normal")
        q = get_random_question()

        if not q.get("question") or "ошибка" in q.get("answer", "").lower():
            await query.message.reply_text("⚠️ Ошибка при загрузке вопроса. Попробуйте ещё раз позже.")
            return
        
        user_sessions[user_id] = {"q": q, "mode": mode, "answered": False}
        
        image_urls = q.get("image_urls", [])
        
        question_text = f"❓ Вопрос:\n{q['question']}"
        
        if q.get("metadata_text"):
            question_text += f"\n\n{q['metadata_text']}"
        
        if q.get("question_url"):
            question_text += f"\n\n🔗 [Ссылка на вопрос в базе]({q['question_url']})"
        
        if image_urls:
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
            
            for img_url in image_urls:
                try:
                    await query.message.reply_photo(
                        photo=img_url,
                        caption="📷 Изображение к вопросу"
                    )
                except Exception as e:
                    logging.error(f"Error sending image {img_url}: {e}")
                    await query.message.reply_text(f"⚠️ Не удалось загрузить изображение: {img_url}")
        else:
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
        
        await start_timer(query.message.chat_id, context, user_id, q, mode)
        
    if query.data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🎮 Выбрать режим", callback_data="choose_mode")],
            [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
            [InlineKeyboardButton("🏆 Рейтинг", callback_data="show_rating")]
        ]
        await query.edit_message_text("Главное меню Brain Blast 🧠⚡", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    name = update.message.from_user.full_name
    user_answer = update.message.text.strip()
    chat_id = update.message.chat_id
    
    logging.info(f"🚨 ANSWER RECEIVED from user {user_id}: '{user_answer}'")
    
    session = user_sessions.get(user_id)
    if not session or "q" not in session:
        await context.bot.send_message(
            chat_id=chat_id,
            text="У вас нет активного вопроса. Нажмите 'Новый вопрос', чтобы начать."
        )
        return
    
    try:
        await context.bot.send_message(chat_id=chat_id, text="⏳ Проверяю ваш ответ...")
    except Exception as e:
        logging.error(f"Failed to send acknowledgment: {e}")
    
    q = session["q"]
    correct_answer = q.get("answer", "")
    comment = q.get("comment") or "Без комментария."
    
    if session.get("timer_expired", False):
        logging.info(f"Timer already expired for user {user_id}, treating as late answer")
        
        is_correct = check_answer(user_answer, correct_answer)
        
        if is_correct:
            keyboard = [
                [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
            ]
            
            try:
                increment_score(user_id, name)
                logging.info(f"Score incremented for late but correct answer from user {user_id}")
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ ПРАВИЛЬНО! Хотя время истекло, очко вам засчитано!\n\n📝 Ответ: {correct_answer}\n💬 {comment}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logging.error(f"Failed to send late correct answer message: {e}")
                
        else:
            keyboard = [[InlineKeyboardButton("👀 Показать ответ", callback_data=f"reveal_answer:{user_id}")]]
            
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Неверно. Время уже вышло! Вы можете увидеть правильный ответ, нажав на кнопку ниже.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logging.error(f"Failed to send wrong answer after expiration message: {e}")
                
        return
    
    try:
        if session.get("timer_task") and not session.get("timer_task").done():
            session["timer_task"].cancel()
            logging.info(f"Timer cancelled for user {user_id}")
    except Exception as e:
        logging.error(f"Failed to cancel timer: {e}")
    
    logging.info(f"Checking answer: '{user_answer}' against correct: '{correct_answer}'")
    
    user_clean = normalize_answer(user_answer)
    correct_clean = normalize_answer(correct_answer)
    
    logging.info(f"ANSWER CHECK - User: '{user_clean}' vs Correct: '{correct_clean}'")
    
    is_correct = check_answer(user_answer, correct_answer)
    
    if is_correct:
        user_sessions[user_id]["answered"] = True
        user_sessions[user_id]["correct_answer"] = True
        
        try:
            increment_score(user_id, name)
            logging.info(f"Score incremented for user {user_id}")
        except Exception as e:
            logging.error(f"Failed to increment score: {e}")
        
        keyboard = [
            [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ ПРАВИЛЬНО! Верный ответ.\n\n📝 Ответ: {correct_answer}\n💬 {comment}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Неверно, попробуйте еще раз!"
        )

def check_answer(user_answer, correct_answer):
    if not user_answer or not correct_answer:
        return False
        
    user_clean = normalize_answer(user_answer)
    correct_clean = normalize_answer(correct_answer)
    
    logging.info(f"ANSWER CHECK - User: '{user_clean}' vs Correct: '{correct_clean}'")
    
    if user_clean == correct_clean:
        logging.info("✓ MATCH: Exact match after normalization")
        return True
    
    user_words = set(user_clean.split())
    correct_words = set(correct_clean.split())
    
    if len(correct_words) > 1 and len(user_words) > 1:
        common_words = correct_words.intersection(user_words)
        match_percentage = len(common_words) / len(correct_words)
        logging.info(f"Keywords match: {match_percentage:.2f} - {len(common_words)}/{len(correct_words)} words")
        
        if match_percentage >= 0.75:
            logging.info("✓ MATCH: High keyword overlap")
            return True
            
        if match_percentage >= 0.5 and len(common_words) >= 2:
            important_words = [w for w in correct_words if len(w) > 3]
            important_matches = [w for w in common_words if len(w) > 3]
            
            if len(important_matches) >= len(important_words) * 0.6:
                logging.info("✓ MATCH: Important keywords match")
                return True
    
    if (user_clean in correct_clean) or (correct_clean in user_clean):
        logging.info("✓ MATCH: One answer contains the other")
        return True
        
    if len(user_clean) > 10 and len(correct_clean) > 10:
        user_word_list = user_clean.split()
        correct_word_list = correct_clean.split()
        
        max_matching_seq = 0
        current_matching_seq = 0
        
        for user_word in user_word_list:
            if user_word in correct_word_list:
                current_matching_seq += 1
                max_matching_seq = max(max_matching_seq, current_matching_seq)
            else:
                current_matching_seq = 0
                
        if max_matching_seq >= 3:
            logging.info(f"✓ MATCH: Found contiguous sequence of {max_matching_seq} matching words")
            return True
            
    socrates_concepts = [
        ["знает", "знают", "знание", "знали"],
        ["меньше", "ничего", "не", "хуже"],
        ["другие", "остальные", "все"]
    ]
    
    concept_matches = 0
    for concept_group in socrates_concepts:
        if any(concept in user_clean for concept in concept_group):
            concept_matches += 1
    
    if concept_matches == len(socrates_concepts):
        logging.info("✓ MATCH: All key concepts present in the answer")
        return True
    
    if "никто ничего не знает" in user_clean and "он" in user_clean:
        logging.info("✓ MATCH: Special case for Socrates question")
        return True
        
    if "другие знают еще меньше" in user_clean:
        logging.info("✓ MATCH: Special case for Socrates question (variant 2)")
        return True
        
    return False

async def start_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_modes[user_id] = "drift"
    
    if user_id in drift_sessions:
        del drift_sessions[user_id]
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    start_word = start_drift_session(user_id)
    
    await update.message.reply_text(
        f"🌊 *Ассоциативный дрифт* запущен!\n\n"
        f"Я начинаю с: *{start_word}*\n\n"
        f"Напиши свою ассоциацию к этому слову.\n"
        f"Введи /stop чтобы завершить сессию.",
        parse_mode="Markdown"
    )

async def stop_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if user_id not in drift_sessions:
        await update.message.reply_text("У вас нет активной сессии ассоциативного дрифта.")
        return
    
    chain = stop_drift_session(user_id)
    
    formatted_chain = ' → '.join(chain)
    
    keyboard = [
        [InlineKeyboardButton("🌊 Новый дрифт", callback_data="set_mode:drift")],
        [InlineKeyboardButton("🎮 Выбрать режим", callback_data="choose_mode")],
        [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")]
    ]
    
    await update.message.reply_text(
        f"🏁 Сессия ассоциативного дрифта завершена!\n\n"
        f"Цепочка ассоциаций:\n*{formatted_chain}*\n\n"
        f"Что хотите делать дальше?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text.strip()
    chat_id = update.message.chat_id
    
    import datetime
    now = datetime.datetime.now()
    timestamp = now.strftime("%H:%M:%S.%f")[:-3]
    
    logging.info(f"🔍 INCOMING MESSAGE from user {user_id} at {timestamp}: '{message_text}'")
    
    session = user_sessions.get(user_id, {})
    if session:
        session["input_processing"] = True
        logging.info(f"⚠️ [SYNC] Set input_processing flag for user {user_id}")
        
        if session.get("timer_expired"):
            timer_expired_time = session.get("timer_expired_timestamp", 0)
            current_time = datetime.datetime.now().timestamp()
            time_difference = current_time - timer_expired_time
            
            if time_difference < 2.0:
                logging.info(f"⚠️ [TIMING] Answer received {time_difference:.2f} seconds after timer expiration - applying grace period")
                session["timer_expired"] = False
                logging.info(f"⚠️ [TIMING] Reset timer_expired flag for borderline answer")
        
    if session and session.get("timer_task") and not session.get("timer_task").done():
        try:
            session["timer_task"].cancel()
            logging.info(f"⚠️ [SYNC] Timer cancelled for user {user_id}")
            await asyncio.sleep(1.0)
        except Exception as e:
            logging.error(f"Failed to cancel timer during immediate check: {e}")
    
    if session and session.get("timer_expired") and not session.get("answered", False):
        logging.info(f"⚠️ [SYNC] CRITICAL: Detected last-millisecond answer after timer expiration!")
        session["timer_expired"] = False
        if session.get("timer_task"):
            try:
                session["timer_task"].cancel()
                logging.info(f"⚠️ [SYNC] Cancelled scheduled answer reveal")
            except Exception as e:
                logging.error(f"Failed to cancel scheduled answer reveal: {e}")
    
    try:
        if user_modes.get(user_id) == "drift" and user_id in drift_sessions:
            if message_text.startswith('/'):
                if session:
                    session["input_processing"] = False
                
                if message_text.startswith('/start'):
                    chain = stop_drift_session(user_id)
                    
                    formatted_chain = ' → '.join(chain)
                    
                    keyboard = [
                        [InlineKeyboardButton("🎮 Выбрать режим", callback_data="choose_mode")],
                        [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")]
                    ]
                    
                    await update.message.reply_text(
                        f"🏁 Сессия ассоциативного дрифта завершена командой /start\n\n"
                        f"Цепочка ассоциаций:\n*{formatted_chain}*\n\n"
                        f"Что хотите делать дальше?",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="Markdown"
                    )
                    return
                return
                
            next_word = add_association(user_id, message_text)
            
            if session:
                session["input_processing"] = False
                
            await update.message.reply_text(
                f"👉 *{next_word}*",
                parse_mode="Markdown"
            )
            return
        
        await process_answer_with_priority(update, context)
    finally:
        if user_id in user_sessions:
            user_sessions[user_id]["input_processing"] = False
            logging.info(f"⚠️ [SYNC] Reset input_processing flag for user {user_id}")
        else:
            logging.info(f"⚠️ [SYNC] Cannot reset input_processing - session not found for user {user_id}")

async def process_answer_with_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    name = update.message.from_user.full_name
    user_answer = update.message.text.strip()
    chat_id = update.message.chat_id
    
    session = user_sessions.get(user_id)
    if not session or "q" not in session:
        await context.bot.send_message(
            chat_id=chat_id,
            text="У вас нет активного вопроса. Нажмите 'Новый вопрос', чтобы начать."
        )
        return
    
    try:
        await context.bot.send_message(chat_id=chat_id, text="⏳ Проверяю ваш ответ...")
    except Exception as e:
        logging.error(f"Failed to send acknowledgment: {e}")
    
    q = session["q"]
    correct_answer = q.get("answer", "")
    comment = q.get("comment") or "Без комментария."
    
    is_correct = check_answer(user_answer, correct_answer)
    logging.info(f"Priority answer check result: {is_correct} for user {user_id}")
    
    if is_correct:
        user_sessions[user_id]["answered"] = True
        user_sessions[user_id]["correct_answer"] = True
        
        try:
            increment_score(user_id, name)
            logging.info(f"Score incremented for user {user_id}")
        except Exception as e:
            logging.error(f"Failed to increment score: {e}")
        
        keyboard = [
            [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ ПРАВИЛЬНО! Верный ответ.\n\n📝 Ответ: {correct_answer}\n💬 {comment}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        if session.get("timer_expired", False):
            keyboard = [[InlineKeyboardButton("👀 Показать ответ", callback_data=f"reveal_answer:{user_id}")]]
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Неверно. Время уже вышло! Вы можете увидеть правильный ответ, нажав на кнопку ниже.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Неверно, попробуйте еще раз!"
            )

def main():
    init_db()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("drift", start_drift))
    application.add_handler(CommandHandler("stop", stop_drift))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Starting bot...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

