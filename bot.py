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
        logging.info(f"Processing reveal_answer for user {user_id}, pressed by {query.from_user.id}")
        
        # Get the chat ID directly from the query
        chat_id = query.message.chat_id
        
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
            
            try:
                # First try to cancel any active timer for this user
                if session.get("timer_task") and not session.get("timer_task").done():
                    try:
                        session["timer_task"].cancel()
                        session["timer_task"] = None
                        logging.info(f"Timer cancelled for user {user_id} when showing answer")
                    except Exception as e:
                        logging.error(f"Failed to cancel timer: {e}")
                
                # Mark as answered to prevent duplicate answer processing
                user_sessions[user_id]["answered"] = True
                
                # Send answer directly using context.bot instead of query.message.reply_text
                message_text = f"📝 Ответ: {answer}\n💬 {comment}"
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text, 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                logging.info(f"✅ Successfully sent answer reveal message to chat {chat_id}")
            except Exception as e:
                logging.error(f"❌ Error sending reveal answer message: {e}", exc_info=True)
                try:
                    # Try with simpler message if fails
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"📝 Ответ: {answer}"
                    )
                    logging.info("Sent simplified answer message as fallback")
                except Exception as e2:
                    logging.error(f"Failed to send even simple answer reveal: {e2}")
        else:
            logging.warning(f"Session not found for user {user_id} in reveal_answer")
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Не удалось найти информацию о вопросе. Попробуйте запросить новый вопрос."
                )
            except Exception as e:
                logging.error(f"Failed to send session not found message: {e}")

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
    Enhanced answer handler that fixes all response issues
    """
    # Get user information and message
    user_id = update.message.from_user.id
    name = update.message.from_user.full_name
    user_answer = update.message.text.strip()
    chat_id = update.message.chat_id
    
    logging.info(f"⚡ NEW ANSWER: User {user_id} ({name}) submitted: '{user_answer}'")
    
    # Check if user has an active question session
    session = user_sessions.get(user_id)
    if not session or "q" not in session:
        logging.error(f"No active question session for user {user_id}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="🤔 У вас сейчас нет активного вопроса. Нажмите 'Новый вопрос', чтобы начать."
        )
        return
    
    # Check if the user already answered correctly
    if session.get("correct_answer", False):
        logging.info(f"User {user_id} already answered correctly")
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Вы уже ответили верно на этот вопрос."
        )
        return
    
    # Send immediate acknowledgment message before we do any processing
    ack_message = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ Твой ответ принят и обрабатывается..."
    )
    logging.info(f"Sent acknowledgment message to user {user_id}")

    # Cancel timer task IMMEDIATELY to prevent "Time's up" message
    if session.get("timer_task") and not session.get("timer_task").done():
        try:
            session["timer_task"].cancel()
            session["timer_task"] = None
            logging.info(f"Timer cancelled for user {user_id}")
        except Exception as e:
            logging.error(f"Failed to cancel timer: e")
    
    # Get the correct answer
    correct_answer = session["q"].get("answer", "")
    if not correct_answer:
        logging.error(f"No correct answer found in question data for user {user_id}")
        return
        
    logging.info(f"Comparing user answer '{user_answer}' with correct answer '{correct_answer}'")
    
    # Check if answer is correct using various methods
    is_correct = False
    clean_user = normalize_answer(user_answer)
    clean_correct = normalize_answer(correct_answer)
    
    # Different matching strategies
    if clean_user == clean_correct:
        is_correct = True
        logging.info("MATCH: Exact match after normalization")
    elif clean_user in clean_correct or clean_correct in clean_user:
        is_correct = True
        logging.info("MATCH: One contains the other")
    elif len(clean_user) > 3 and len(clean_correct) > 3 and (clean_user in clean_correct or clean_correct in clean_user):
        is_correct = True
        logging.info("MATCH: Partial match for longer answers")
    
    # Process the result
    if is_correct:
        # Mark as correctly answered and update score
        user_sessions[user_id]["answered"] = True
        user_sessions[user_id]["correct_answer"] = True
        
        # Update user score
        increment_score(user_id, name)
        
        # Get comment if available
        comment = session["q"].get("comment") or "Без комментария."
        
        # Send confirmation message
        response_text = f"✅ Правильно! Вы ответили верно.\n\n📝 Ответ: {correct_answer}\n💬 {comment}"
        
        try:
            # Send direct message using context.bot
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=response_text
            )
            logging.info(f"✅ SENT correct answer confirmation to user {user_id}")
            
            # Double-check if message was sent
            if not sent:
                logging.warning(f"Message not sent despite no exception")
                # Try simple message as fallback
                await context.bot.send_message(chat_id=chat_id, text="✅ Правильно!")
                
        except Exception as e:
            logging.error(f"❌ ERROR sending correct answer confirmation: {e}")
            # Try with a simpler message
            try:
                await context.bot.send_message(chat_id=chat_id, text="✅ Правильно!")
            except Exception as e2:
                logging.error(f"❌ ERROR sending even simple message: {e2}")
    else:
        # Allow retry for incorrect answers
        user_sessions[user_id]["answered"] = False
        
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Неверно, попробуйте еще раз!"
            )
            logging.info(f"Sent incorrect message to user {user_id}")
        except Exception as e:
            logging.error(f"Error sending incorrect message: {e}")

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

