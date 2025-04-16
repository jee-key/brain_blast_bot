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
                # Use context.bot.send_message instead of query.message.reply_text
                await context.bot.send_message(
                    chat_id,
                    f"📝 Ответ: {answer}\n💬 {comment}", 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                logging.info(f"Sent answer reveal message to user {user_id}")
            except Exception as e:
                logging.error(f"Error sending reveal answer message: {e}", exc_info=True)
                try:
                    # Try with simpler message if fails
                    await context.bot.send_message(
                        chat_id,
                        f"📝 Ответ: {answer}"
                    )
                except Exception as e2:
                    logging.error(f"Failed to send even simple answer reveal: {e2}")
        else:
            logging.warning(f"Session not found for user {user_id} in reveal_answer")
            try:
                await context.bot.send_message(
                    chat_id,
                    "⚠️ Не удалось найти информацию о вопросе. Попробуйте запросить новый вопрос."
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
    Completely rewritten handle_answer function to fix all issues with answer processing
    """
    # Basic info extraction
    user_id = update.message.from_user.id
    name = update.message.from_user.full_name
    user_answer = update.message.text.strip()
    chat_id = update.message.chat_id
    
    logging.info(f"ANSWER RECEIVED: User {user_id} ({name}) submitted: '{user_answer}'")
    
    # 1. Check if user has an active session
    session = user_sessions.get(user_id)
    if not session or "q" not in session:
        logging.error(f"No active session/question for user {user_id}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="🤔 У вас нет активного вопроса. Нажмите 'Новый вопрос', чтобы начать."
        )
        return
    
    # 2. Check if user already answered correctly
    if session.get("correct_answer", False):
        logging.info(f"User {user_id} already answered correctly")
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Вы уже ответили верно на этот вопрос."
        )
        return
        
    # Get the correct answer from the session
    correct_answer = session["q"].get("answer", "")
    if not correct_answer:
        logging.error(f"No answer found in question for user {user_id}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Произошла ошибка: не найден правильный ответ. Попробуйте запросить новый вопрос."
        )
        return
        
    logging.info(f"Checking user answer: '{user_answer}' against correct: '{correct_answer}'")
    
    # 3. Process the answer - completely rewritten logic
    is_correct = False
    
    # 3.1 Handle duplex (multi-part) questions
    if "1." in correct_answer and "2." in correct_answer:
        logging.info(f"Processing multi-part (duplex) question")
        
        # Direct match first
        if user_answer.lower() == correct_answer.lower():
            is_correct = True
            logging.info("CORRECT: Direct match on multi-part question")
        
        # If not direct match, try part-by-part matching
        elif "1." in user_answer and "2." in user_answer:
            # Extract parts from both answers
            correct_parts = re.split(r'\d\.', correct_answer.lower())
            user_parts = re.split(r'\d\.', user_answer.lower()) 
            
            # Remove empty parts
            correct_parts = [p.strip() for p in correct_parts if p.strip()]
            user_parts = [p.strip() for p in user_parts if p.strip()]
            
            # Check if we have the same number of parts
            if len(correct_parts) == len(user_parts):
                # Check each part
                all_parts_match = True
                for i in range(len(correct_parts)):
                    c_part = normalize_answer(correct_parts[i])
                    u_part = normalize_answer(user_parts[i])
                    
                    # If any part doesn't match, the answer is wrong
                    if not (u_part == c_part or u_part in c_part or c_part in u_part):
                        all_parts_match = False
                        break
                
                if all_parts_match:
                    is_correct = True
                    logging.info("CORRECT: All parts of multi-part question match")
    
    # 3.2 Handle regular (single) answers
    else:
        clean_correct = normalize_answer(correct_answer)
        clean_user = normalize_answer(user_answer)
        
        # Direct normalization match
        if clean_user == clean_correct:
            is_correct = True
            logging.info("CORRECT: Exact normalized match")
        
        # One contains the other
        elif clean_user in clean_correct or clean_correct in clean_user:
            is_correct = True
            logging.info("CORRECT: One answer contains the other")
            
        # Raw lowercased match
        elif user_answer.lower() == correct_answer.lower():
            is_correct = True
            logging.info("CORRECT: Raw lowercase match")
            
        # Keyword matching for multi-word answers
        elif ' ' in clean_correct and ' ' in clean_user:
            correct_keywords = set(clean_correct.split())
            user_keywords = set(clean_user.split())
            
            if len(correct_keywords) > 1 and len(user_keywords) > 0:
                common_words = correct_keywords.intersection(user_keywords)
                match_percentage = len(common_words) / len(correct_keywords)
                logging.info(f"Keyword match percentage: {match_percentage:.2f}")
                
                if match_percentage >= 0.7:
                    is_correct = True
                    logging.info("CORRECT: Keyword match percentage >= 70%")
        
        # Check comment for alternative answers
        if not is_correct:
            comment = session["q"].get("comment", "").lower()
            if comment and clean_user in comment:
                acceptance_indicators = [
                    "также принимается", "засчитывать", "принимать", 
                    "зачет", "зачёт", "зачитывать", "эквивалент"
                ]
                for indicator in acceptance_indicators:
                    if indicator in comment:
                        is_correct = True
                        logging.info(f"CORRECT: Alternative answer accepted based on comment")
                        break
    
    # 4. Cancel timer if it exists
    if session.get("timer_task") and not session.get("timer_task").done():
        try:
            session["timer_task"].cancel()
            session["timer_task"] = None
            logging.info(f"Timer cancelled for user {user_id}")
        except Exception as e:
            logging.error(f"Error cancelling timer: {e}")
    
    # 5. Process result and send feedback
    if is_correct:
        # Update session
        user_sessions[user_id]["answered"] = True
        user_sessions[user_id]["correct_answer"] = True
        
        # Update score
        increment_score(user_id, name)
        logging.info(f"Score incremented for user {user_id}")
        
        # Prepare response
        comment = session["q"].get("comment", "Без комментария.")
        response = f"✅ Правильно! Вы ответили верно.\n\n📝 Ответ: {correct_answer}\n💬 {comment}"
        
        # Send response
        try:
            await context.bot.send_message(chat_id=chat_id, text=response)
            logging.info(f"SENT CORRECT ANSWER MESSAGE to user {user_id}")
        except Exception as e:
            logging.error(f"Failed to send correct answer message: {e}")
            # Try simpler message
            try:
                await context.bot.send_message(chat_id=chat_id, text="✅ Правильно!")
            except:
                logging.error("Failed to send even simple correct message")
    else:
        # Allow user to try again
        user_sessions[user_id]["answered"] = False
        
        # Send incorrect message
        try:
            await context.bot.send_message(chat_id=chat_id, text="❌ Неверно, попробуйте еще раз!")
            logging.info(f"SENT INCORRECT MESSAGE to user {user_id}")
        except Exception as e:
            logging.error(f"Failed to send incorrect message: {e}")

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

