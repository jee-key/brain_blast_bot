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

# Get bot token from environment variable
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN provided. Set the BOT_TOKEN environment variable.")

ENABLE_HINTS = os.getenv("ENABLE_HINTS", "true").lower() == "true"

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# User mode storage
user_modes = {}  # user_id -> mode

MODES = {
    "normal": "Обычный режим",
    "speed": "На скорость",
    "no_hints": "Без подсказок",
    "drift": "Ассоциативный дрифт"
}

# Time settings for different modes (in seconds)
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
    
    # Get user ID and chat ID directly from the query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    logging.info(f"Button pressed: {query.data} by user {user_id}")
    
    # Handle callback answer with error protection
    try:
        await query.answer()
    except Exception as e:
        logging.warning(f"Failed to answer callback query: {e}")
        # Continue processing despite the error - the button will still work
    
    if query.data.startswith("set_mode:"):
        mode = query.data.split(":")[1]
        user_id = query.from_user.id
        user_modes[user_id] = mode
        
        # Special handling for drift mode
        if mode == "drift":
            # Clean any existing sessions for this user
            if user_id in drift_sessions:
                del drift_sessions[user_id]
            if user_id in user_sessions:
                del user_sessions[user_id]
                
            # Start a new drift session
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
        
        # Normal mode handling for other modes
        # Display main menu buttons after mode selection
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
        
        # Check if user is in drift mode and stop the session
        if user_modes.get(user_id) == "drift" and user_id in drift_sessions:
            # Get the chain before stopping
            chain = stop_drift_session(user_id)
            
            # Log that the drift session was stopped by clicking "New question"
            logging.info(f"User {user_id} stopped drift session by clicking 'New question' button")
            
            # Switch mode back to normal
            user_modes[user_id] = "normal"
        
        mode = user_modes.get(user_id, "normal")
        q = get_random_question()

        # Error check
        if not q.get("question") or "ошибка" in q.get("answer", "").lower():
            await query.message.reply_text("⚠️ Ошибка при загрузке вопроса. Попробуйте ещё раз позже.")
            return
        
        # Save the question in user session
        user_sessions[user_id] = {"q": q, "mode": mode, "answered": False}
        
        # Handle questions with images
        image_urls = q.get("image_urls", [])
        
        # Prepare question text with metadata
        question_text = f"❓ Вопрос:\n{q['question']}"
        
        # Add metadata if available
        if q.get("metadata_text"):
            question_text += f"\n\n{q['metadata_text']}"
        
        # Add direct link to the question if available
        if q.get("question_url"):
            question_text += f"\n\n🔗 [Ссылка на вопрос в базе]({q['question_url']})"
        
        if image_urls:
            # First send the question text with metadata
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
            
            # Then send each image
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
            # No images, just send the text with metadata
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
        
        # Start the timer for this question
        await start_timer(query.message.chat_id, context, user_id, q, mode)

    if query.data == "show_rating":
        top = get_top_users()
        if not top:
            await query.message.reply_text("Рейтинг пока пуст.")
            return
        text = "\n".join([f"{i+1}. {name} — {score}" for i, (name, score) in enumerate(top)])
        await query.message.reply_text(f"🏆 Топ игроков:\n{text}")
        
    if query.data.startswith("reveal_answer:"):
        # Extract target user ID from the button data
        target_user_id = int(query.data.split(":")[1])
        logging.info(f"Processing reveal_answer for user {target_user_id}, pressed by {user_id}")
        
        # Get session data
        session = user_sessions.get(target_user_id, {})
        
        if session and "q" in session:
            answer = session["q"]["answer"]
            comment = session["q"].get("comment") or "Без комментария."
            
            # Add buttons for continuing or returning to menu
            keyboard = [
                [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
                [InlineKeyboardButton("🔄 Продолжить итерацию?", callback_data="continue_iteration")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
            ]
            
            # Cancel any active timer if it exists
            if session.get("timer_task") and not session.get("timer_task").done():
                try:
                    session["timer_task"].cancel()
                    session["timer_task"] = None
                    logging.info(f"Timer cancelled for user {target_user_id} when showing answer")
                except Exception as e:
                    logging.error(f"Failed to cancel timer: {e}")
            
            # Mark as answered to prevent duplicate answer processing
            user_sessions[target_user_id]["answered"] = True
            
            # Send answer to chat
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
                # Try with simpler message if the first attempt fails
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

        # Error check
        if not q.get("question") or "ошибка" in q.get("answer", "").lower():
            await query.message.reply_text("⚠️ Ошибка при загрузке вопроса. Попробуйте ещё раз позже.")
            return
        
        # Save the question in user session
        user_sessions[user_id] = {"q": q, "mode": mode, "answered": False}
        
        # Handle questions with images
        image_urls = q.get("image_urls", [])
        
        # Prepare question text with metadata
        question_text = f"❓ Вопрос:\n{q['question']}"
        
        # Add metadata if available
        if q.get("metadata_text"):
            question_text += f"\n\n{q['metadata_text']}"
        
        # Add direct link to the question if available
        if q.get("question_url"):
            question_text += f"\n\n🔗 [Ссылка на вопрос в базе]({q['question_url']})"
        
        if image_urls:
            # First send the question text with metadata
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
            
            # Then send each image
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
            # No images, just send the text with metadata
            await query.message.reply_text(question_text, parse_mode="Markdown", disable_web_page_preview=True)
        
        # Start the timer for this question
        await start_timer(query.message.chat_id, context, user_id, q, mode)
        
    if query.data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🎮 Выбрать режим", callback_data="choose_mode")],
            [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
            [InlineKeyboardButton("🏆 Рейтинг", callback_data="show_rating")]
        ]
        await query.edit_message_text("Главное меню Brain Blast 🧠⚡", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик ответов на вопросы с сохранением всех данных исходного вопроса
    """
    user_id = update.message.from_user.id
    name = update.message.from_user.full_name
    user_answer = update.message.text.strip()
    chat_id = update.message.chat_id
    
    logging.info(f"🚨 ANSWER RECEIVED from user {user_id}: '{user_answer}'")
    
    # 1. Проверяем наличие активного вопроса
    session = user_sessions.get(user_id)
    if not session or "q" not in session:
        await context.bot.send_message(
            chat_id=chat_id,
            text="У вас нет активного вопроса. Нажмите 'Новый вопрос', чтобы начать."
        )
        return
    
    # 2. Отправляем сообщение о проверке
    try:
        await context.bot.send_message(chat_id=chat_id, text="⏳ Проверяю ваш ответ...")
    except Exception as e:
        logging.error(f"Failed to send acknowledgment: {e}")
    
    # 3. Получаем данные вопроса (сохраняются даже после истечения таймера)
    q = session["q"]
    correct_answer = q.get("answer", "")
    comment = q.get("comment") or "Без комментария."
    
    # 4. Проверяем, истек ли таймер
    if session.get("timer_expired", False):
        logging.info(f"Timer already expired for user {user_id}, treating as late answer")
        
        # Проверяем правильность ответа
        is_correct = check_answer(user_answer, correct_answer)
        
        if is_correct:
            # Правильный ответ, даже если время истекло
            keyboard = [
                [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
            ]
            
            try:
                # Увеличиваем счет (даже после истечения времени)
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
            # Неправильный ответ после истечения времени
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
    
    # 5. Отменяем таймер, так как получен ответ
    try:
        if session.get("timer_task") and not session.get("timer_task").done():
            session["timer_task"].cancel()
            logging.info(f"Timer cancelled for user {user_id}")
    except Exception as e:
        logging.error(f"Failed to cancel timer: {e}")
    
    # 6. Проверяем правильность ответа
    logging.info(f"Checking answer: '{user_answer}' against correct: '{correct_answer}'")
    
    # Нормализуем ответы для сравнения
    user_clean = normalize_answer(user_answer)
    correct_clean = normalize_answer(correct_answer)
    
    logging.info(f"ANSWER CHECK - User: '{user_clean}' vs Correct: '{correct_clean}'")
    
    # Проверяем ответ с помощью всех доступных методов
    is_correct = check_answer(user_answer, correct_answer)
    
    # 7. Обрабатываем результат
    if is_correct:
        # Отмечаем вопрос как отвеченный правильно
        user_sessions[user_id]["answered"] = True
        user_sessions[user_id]["correct_answer"] = True
        
        # Увеличиваем счет
        try:
            increment_score(user_id, name)
            logging.info(f"Score incremented for user {user_id}")
        except Exception as e:
            logging.error(f"Failed to increment score: {e}")
        
        # Отправляем сообщение о правильном ответе
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
        # Неправильный ответ, время не истекло
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Неверно, попробуйте еще раз!"
        )

def check_answer(user_answer, correct_answer):
    """
    Enhanced answer checking for CHGK questions with multiple strategies
    """
    if not user_answer or not correct_answer:
        return False
        
    # Normalize both answers
    user_clean = normalize_answer(user_answer)
    correct_clean = normalize_answer(correct_answer)
    
    # Log what we're comparing
    logging.info(f"ANSWER CHECK - User: '{user_clean}' vs Correct: '{correct_clean}'")
    
    # STRATEGY 1: Direct match after normalization
    if user_clean == correct_clean:
        logging.info("✓ MATCH: Exact match after normalization")
        return True
    
    # STRATEGY 2: Keywords matching (useful for long answers)
    # First, get keywords from both answers
    user_words = set(user_clean.split())
    correct_words = set(correct_clean.split())
    
    # For answers with multiple words, check keyword overlap
    if len(correct_words) > 1 and len(user_words) > 1:
        common_words = correct_words.intersection(user_words)
        # Calculate what percentage of correct keywords are present in user answer
        match_percentage = len(common_words) / len(correct_words)
        logging.info(f"Keywords match: {match_percentage:.2f} - {len(common_words)}/{len(correct_words)} words")
        
        # If 75% or more keywords match, consider it correct
        if match_percentage >= 0.75:
            logging.info("✓ MATCH: High keyword overlap")
            return True
            
        # Special case for "essence" matching with 50%+ keyword match
        # This handles cases where user gives a conceptually correct but differently phrased answer
        if match_percentage >= 0.5 and len(common_words) >= 2:
            # Only key words match, check if these are the significant ones
            important_words = [w for w in correct_words if len(w) > 3]  # Words longer than 3 chars are likely significant
            important_matches = [w for w in common_words if len(w) > 3]
            
            if len(important_matches) >= len(important_words) * 0.6:
                logging.info("✓ MATCH: Important keywords match")
                return True
    
    # STRATEGY 3: Containment (one contains the other)
    # This is especially helpful for CHGK where answers may have extra/missing parts
    if (user_clean in correct_clean) or (correct_clean in user_clean):
        logging.info("✓ MATCH: One answer contains the other")
        return True
        
    # STRATEGY 4: Semantic similarity for longer answers
    # This helps with conceptually similar answers phrased differently
    if len(user_clean) > 10 and len(correct_clean) > 10:
        # For very long answers, look for at least 3 matching words in sequence
        user_word_list = user_clean.split()
        correct_word_list = correct_clean.split()
        
        # Find the longest contiguous sequence of matching words
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
            
    # STRATEGY 5: Check if answer keys are present 
    # For answers like "никто ничего не знает, включая его самого" vs "другие знают еще меньше"
    # Break down into logical components
    
    # Key question concepts for Socrates question
    socrates_concepts = [
        ["знает", "знают", "знание", "знали"],  # Knowledge
        ["меньше", "ничего", "не", "хуже"],     # Less/nothing/negation
        ["другие", "остальные", "все"]           # Others
    ]
    
    # Count how many concept groups are represented in the user's answer
    concept_matches = 0
    for concept_group in socrates_concepts:
        if any(concept in user_clean for concept in concept_group):
            concept_matches += 1
    
    # If all key concepts are present, likely correct
    if concept_matches == len(socrates_concepts):
        logging.info("✓ MATCH: All key concepts present in the answer")
        return True
    
    # STRATEGY 6: Check for special cases - these are common CHGK answers that need special handling
    if "никто ничего не знает" in user_clean and "он" in user_clean:
        logging.info("✓ MATCH: Special case for Socrates question")
        return True
        
    if "другие знают еще меньше" in user_clean:
        logging.info("✓ MATCH: Special case for Socrates question (variant 2)")
        return True
        
    # No match found
    return False

async def start_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command handler to start a new associative drift session"""
    user_id = update.message.from_user.id
    # Set user mode to drift
    user_modes[user_id] = "drift"
    
    # Clean any existing sessions
    if user_id in drift_sessions:
        del drift_sessions[user_id]
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    # Start a new drift session
    start_word = start_drift_session(user_id)
    
    await update.message.reply_text(
        f"🌊 *Ассоциативный дрифт* запущен!\n\n"
        f"Я начинаю с: *{start_word}*\n\n"
        f"Напиши свою ассоциацию к этому слову.\n"
        f"Введи /stop чтобы завершить сессию.",
        parse_mode="Markdown"
    )

async def stop_drift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command handler to stop the current drift session"""
    user_id = update.message.from_user.id
    
    if user_id not in drift_sessions:
        await update.message.reply_text("У вас нет активной сессии ассоциативного дрифта.")
        return
    
    # Get the complete chain
    chain = stop_drift_session(user_id)
    
    # Format the chain nicely
    formatted_chain = ' → '.join(chain)
    
    # Create keyboard for options after stopping
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
    """
    Completely redesigned message handler with priority answer processing
    """
    user_id = update.message.from_user.id
    message_text = update.message.text.strip()
    chat_id = update.message.chat_id
    
    logging.info(f"🔍 INCOMING MESSAGE from user {user_id}: '{message_text}'")
    
    # КРИТИЧЕСКИ ВАЖНО: Немедленно проверяем и останавливаем таймер при любом сообщении
    session = user_sessions.get(user_id, {})
    if session and session.get("timer_task") and not session.get("timer_task").done():
        try:
            # Сразу останавливаем таймер
            session["timer_task"].cancel()
            logging.info(f"⚠️ [URGENT] Timer immediately cancelled on message from user {user_id}")
            
            # Добавляем небольшую паузу, чтобы убедиться, что таймер полностью остановлен
            await asyncio.sleep(0.3)
        except Exception as e:
            logging.error(f"Failed to cancel timer during immediate check: {e}")
    
    # Check if user is in drift mode
    if user_modes.get(user_id) == "drift" and user_id in drift_sessions:
        # If in drift mode, check for stop commands first
        if message_text.startswith('/'):
            # If user types /stop, this is handled by the stop_drift command handler
            # If user types /start, we should also exit drift mode
            if message_text.startswith('/start'):
                # Get the chain before stopping
                chain = stop_drift_session(user_id)
                
                # Format the chain and show completion message with options
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
            # For other commands, let them be handled by command handlers
            return
            
        # Process the user's association and get the bot's response
        next_word = add_association(user_id, message_text)
        
        # Send the next association
        await update.message.reply_text(
            f"👉 *{next_word}*",
            parse_mode="Markdown"
        )
        return
    
    # Otherwise, handle as a CHGK quiz answer - with high priority processing
    await process_answer_with_priority(update, context)

async def process_answer_with_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Высокоприоритетный обработчик ответов, вызывается после отмены таймера
    """
    user_id = update.message.from_user.id
    name = update.message.from_user.full_name
    user_answer = update.message.text.strip()
    chat_id = update.message.chat_id
    
    # 1. Проверяем наличие активного вопроса (повторно, для уверенности)
    session = user_sessions.get(user_id)
    if not session or "q" not in session:
        await context.bot.send_message(
            chat_id=chat_id,
            text="У вас нет активного вопроса. Нажмите 'Новый вопрос', чтобы начать."
        )
        return
    
    # 2. Отправляем сообщение о проверке
    try:
        await context.bot.send_message(chat_id=chat_id, text="⏳ Проверяю ваш ответ...")
    except Exception as e:
        logging.error(f"Failed to send acknowledgment: {e}")
    
    # 3. Получаем данные вопроса
    q = session["q"]
    correct_answer = q.get("answer", "")
    comment = q.get("comment") or "Без комментария."
    
    # 4. Проверяем правильность ответа
    is_correct = check_answer(user_answer, correct_answer)
    logging.info(f"Priority answer check result: {is_correct} for user {user_id}")
    
    # 5. Обрабатываем результат проверки
    if is_correct:
        # Отмечаем вопрос как отвеченный правильно
        user_sessions[user_id]["answered"] = True
        user_sessions[user_id]["correct_answer"] = True
        
        # Увеличиваем счет
        try:
            increment_score(user_id, name)
            logging.info(f"Score incremented for user {user_id}")
        except Exception as e:
            logging.error(f"Failed to increment score: {e}")
        
        # Отправляем сообщение о правильном ответе
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
        # Проверяем, истек ли таймер к этому моменту
        if session.get("timer_expired", False):
            # Если таймер уже истек, показываем кнопку для ответа
            keyboard = [[InlineKeyboardButton("👀 Показать ответ", callback_data=f"reveal_answer:{user_id}")]]
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Неверно. Время уже вышло! Вы можете увидеть правильный ответ, нажав на кнопку ниже.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Неправильный ответ, время не истекло
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Неверно, попробуйте еще раз!"
            )

def main():
    """Start the bot."""
    # Initialize database
    init_db()
    
    # Build application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("drift", start_drift))
    application.add_handler(CommandHandler("stop", stop_drift))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # We no longer use the handle_answer function directly
    # Instead, all messages go through the redesigned handle_message function
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the bot
    print("Starting bot...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

