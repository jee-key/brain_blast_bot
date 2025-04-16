import logging
import os
import re 
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
    await query.answer()

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
        user_id = int(query.data.split(":")[1])
        logging.info(f"Processing reveal_answer for user {user_id}")
        
        # Get session data
        session = user_sessions.get(user_id, {})
        
        if session and "q" in session:
            answer = session["q"]["answer"]
            comment = session["q"].get("comment") or "Без комментария."
            
            # Add buttons for continuing or returning to menu
            keyboard = [
                [InlineKeyboardButton("🎲 Новый вопрос", callback_data="new_question")],
                [InlineKeyboardButton("🔄 Продолжить итерацию?", callback_data="continue_iteration")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
            ]
            
            await query.message.reply_text(
                f"📝 Ответ: {answer}\n💬 {comment}", 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            logging.warning(f"Session not found for user {user_id} in reveal_answer")
            
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
    user_id = update.message.from_user.id
    name = update.message.from_user.full_name
    user_answer = update.message.text.strip().lower()
    session = user_sessions.get(user_id)

    if not session:
        await update.message.reply_text("🤔 У вас сейчас нет активного вопроса. Нажмите 'Новый вопрос', чтобы начать.")
        return

    if session.get("answered", False) or session.get("correct_answer", False):
        await update.message.reply_text("⏳ Твой ответ уже принят, дождитесь следующего вопроса.")
        return

    # Mark the question as answered immediately
    user_sessions[user_id]["answered"] = True

    # Send acknowledgment message immediately
    await update.message.reply_text("⏳ Твой ответ принят и обрабатывается...")

    # Process the answer
    correct_answer = session["q"]["answer"]
    clean_correct = normalize_answer(correct_answer)
    clean_user = normalize_answer(user_answer)
    logging.info(f"Normalized - User: '{clean_user}', Correct: '{clean_correct}'")
    correct_keywords = set(clean_correct.split())
    user_keywords = set(clean_user.split())
    is_correct = False

    if clean_user == clean_correct:
        is_correct = True
        logging.info("Match: Exact after normalization")
    elif clean_user in clean_correct or clean_correct in clean_user:
        is_correct = True
        logging.info("Match: One contains the other")
    elif user_answer.strip().lower() == correct_answer.strip().lower():
        is_correct = True
        logging.info("Match: Raw lowercased answers match")
    elif len(correct_keywords) > 1 and len(user_keywords) > 0:
        common_words = correct_keywords.intersection(user_keywords)
        match_percentage = len(common_words) / len(correct_keywords)
        logging.info(f"Keywords match: {match_percentage:.2f} ({len(common_words)}/{len(correct_keywords)})")
        if match_percentage >= 0.7:
            is_correct = True

    if not is_correct and len(clean_correct) < 15:
        comment = session["q"].get("comment", "").lower()
        if comment and clean_user in comment:
            acceptance_indicators = [
                "также принимается", "засчитывать", "принимать", 
                "зачет", "зачёт", "зачитывать", "эквивалент"
            ]
            for indicator in acceptance_indicators:
                if indicator in comment:
                    is_correct = True
                    logging.info(f"Alternative answer accepted based on comment containing '{indicator}'")
                    break

    # Provide feedback to the user
    if is_correct:
        user_sessions[user_id]["correct_answer"] = True
        increment_score(user_id, name)
        logging.info(f"Incremented score for user {user_id} ({name})")
        comment = session["q"].get("comment") or "Без комментария."
        await update.message.reply_text(
            f"✅ Правильно! Вы ответили верно.\n\n"
            f"📝 Ответ: {session['q']['answer']}\n"
            f"💬 {comment}"
        )
    else:
        await update.message.reply_text("❌ Неверно, попробуйте еще раз!")

    # Ensure no redundant "time's up" message is sent
    session["timer_task"] = None

def get_small_hint(answer):
    """Provides a small hint about the answer without giving too much away"""
    answer = answer.lower()
    
    if len(answer) < 5:
        return f"Ответ состоит из {len(answer)} букв"
    
    # For longer answers, hint at first and last letters
    first = answer[0].upper()
    last = answer[-1]
    
    # For multi-word answers
    if ' ' in answer:
        words = answer.split()
        return f"Ответ состоит из {len(words)} слов"
        
    return f"Ответ начинается на '{first}' и заканчивается на '{last}'"

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
    Main message handler that routes messages to either the CHGK answer handler
    or the associative drift handler based on the user's current mode
    """
    user_id = update.message.from_user.id
    message_text = update.message.text.strip()
    
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
    
    # Otherwise, handle as a CHGK quiz answer
    await handle_answer(update, context)

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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the bot
    print("Starting bot...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

