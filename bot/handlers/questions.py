"""
Question handler for interactive question-answering with buttons.
Allows users to ask questions to OpenCode through an interactive interface.
"""
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Dict, List, Optional, Tuple
import logging
import asyncio
import time
from pathlib import Path

from core.session_manager import session_manager
from core.opencode_proxy import opencode_client
from core.archive_utils import ArchiveCreator
from core.file_tracker import FileChangeTracker
from core import session_files

router = Router()
logger = logging.getLogger("opencode_bot")

class QuestionStates(StatesGroup):
    """States for question answering flow."""
    waiting_for_question = State()
    waiting_for_followup = State()


# Question categories and templates
QUESTION_CATEGORIES = {
    "code_explain": {
        "name": "📝 Объяснить код",
        "template": "Объясни этот код и как он работает:\n\n{code}"
    },
    "code_improve": {
        "name": "🚀 Улучшить код", 
        "template": "Улучши этот код (производительность, читаемость, безопасность):\n\n{code}"
    },
    "code_translate": {
        "name": "🔤 Перевести код",
        "template": "Переведи этот код с {from_lang} на {to_lang}:\n\n{code}"
    },
    "algorithm_explain": {
        "name": "🧮 Объяснить алгоритм",
        "template": "Объясни этот алгоритм и как его можно улучшить:\n\n{code}"
    },
    "bug_find": {
        "name": "🐛 Найти ошибку",
        "template": "Найди ошибки в этом коде и предложи исправления:\n\n{code}"
    },
    "test_write": {
        "name": "🧪 Написать тесты",
        "template": "Напиши тесты для этого кода:\n\n{code}"
    },
    "custom_question": {
        "name": "💭 Свой вопрос",
        "template": None  # User provides custom question
    }
}


async def build_question_categories_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard with question categories."""
    builder = InlineKeyboardBuilder()
    
    for category_id, category_info in QUESTION_CATEGORIES.items():
        builder.add(
            InlineKeyboardButton(
                text=category_info["name"],
                callback_data=f"question_category:{category_id}"
            )
        )
    
    builder.adjust(2)  # 2 buttons per row
    return builder.as_markup()


async def build_followup_questions_keyboard(session_id: str, files: Dict[str, List[str]]) -> Optional[InlineKeyboardMarkup]:
    """Build follow-up question suggestions based on generated files."""
    if not files.get("all"):
        return None
    
    builder = InlineKeyboardBuilder()
    
    # Common follow-up questions
    followup_questions = [
        ("📁 Показать все файлы", "show_files"),
        ("📦 Скачать архив", "download_archive"),
        ("📝 Объяснить код", "explain_code"),
        ("🐛 Отладить код", "debug_code"),
        ("🚀 Оптимизировать", "optimize_code"),
        ("🧪 Добавить тесты", "add_tests"),
    ]
    
    for text, action in followup_questions:
        builder.add(InlineKeyboardButton(text=text, callback_data=f"followup:{action}"))
    
    builder.adjust(2)
    return builder.as_markup()


def extract_code_from_text(text: str) -> str:
    """Extract code from text (handles code blocks)."""
    if '```' in text:
        parts = text.split('```')
        if len(parts) >= 3:
            code_block = parts[1]
            # Remove language specifier
            lines = code_block.split('\n', 1)
            if len(lines) > 1:
                return lines[1].strip()
            return code_block.strip()
    return text.strip()


async def send_files_to_user(message: types.Message, session_folder: str, files: Dict[str, List[str]]) -> None:
    """Send files to user (reusing logic from coding.py)."""
    if not files.get("all"):
        return
    
    from bot.handlers.coding import send_files_to_user as coding_send_files
    session_path = Path(session_folder)
    await coding_send_files(message, session_path, files)


@router.message(Command("ask"))
async def cmd_ask(message: types.Message, state: FSMContext, command: CommandObject):
    """Start interactive question answering."""
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("❌ Вам нужна активная сессия. Используйте /newsession сначала.")
        return
    
    # Check if code is provided in command or message
    code = ""
    if command and command.args:
        code = command.args
    elif message.reply_to_message and message.reply_to_message.text:
        # Check if replying to code
        code = extract_code_from_text(message.reply_to_message.text)
    
    if code:
        # Store code in state
        await state.update_data(question_code=code)
        await state.set_state(QuestionStates.waiting_for_question)
        
        # Show category selection
        keyboard = await build_question_categories_keyboard()
        await message.answer(
            "📝 **Код получен!** Выберите тип вопроса:\n\n"
            f"```python\n{code[:200]}{'...' if len(code) > 200 else ''}\n```",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        # Ask for code
        await state.set_state(QuestionStates.waiting_for_question)
        await message.answer(
            "📝 **Задайте вопрос по коду**\n\n"
            "Отправьте мне код, а затем выберите тип вопроса.\n"
            "Вы можете:\n"
            "1. Отправить код в сообщении с ``` блоками\n"
            "2. Отправить .py файл\n"
            "3. Ответить на сообщение с кодом командой /ask\n\n"
            "Или используйте /cancel чтобы отменить.",
            parse_mode="Markdown"
        )


@router.message(QuestionStates.waiting_for_question)
async def process_question_code(message: types.Message, state: FSMContext):
    """Process code input for question."""
    user_id = message.from_user.id
    
    # Extract code from message
    code = ""
    if message.document and message.document.mime_type and 'text' in message.document.mime_type:
        # Handle file upload
        try:
            assert message.bot is not None
            file = await message.bot.get_file(message.document.file_id)
            if file.file_path:
                file_bytes = await message.bot.download_file(file.file_path)
                assert file_bytes is not None
                code = file_bytes.read().decode('utf-8')
        except Exception as e:
            logger.error(f"Error reading document: {e}")
            await message.answer("❌ Не удалось прочитать файл. Попробуйте ещё раз.")
            return
    elif message.text:
        code = extract_code_from_text(message.text)
    
    if not code:
        await message.answer("❌ Не удалось извлечь код из сообщения. Попробуйте ещё раз.")
        return
    
    # Store code in state
    await state.update_data(question_code=code)
    
    # Show category selection
    keyboard = await build_question_categories_keyboard()
    await message.answer(
        f"✅ **Код получен!** Выберите тип вопроса:\n\n"
        f"```python\n{code[:200]}{'...' if len(code) > 200 else ''}\n```",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("question_category:"))
async def handle_question_category(callback: CallbackQuery, state: FSMContext):
    """Handle question category selection."""
    if callback.message is None:
        await callback.answer()
        return
    
    category_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    if category_id not in QUESTION_CATEGORIES:
        await callback.answer("❌ Неизвестная категория")
        return
    
    category_info = QUESTION_CATEGORIES[category_id]
    
    # Get stored code
    data = await state.get_data()
    code = data.get("question_code", "")
    
    if not code:
        await callback.answer("❌ Код не найден")
        await callback.message.edit_text("❌ Код не найден. Пожалуйста, начните заново.")
        return
    
    # For custom question, ask for question text
    if category_id == "custom_question":
        await state.update_data(question_category=category_id)
        await callback.message.edit_text(
            "💭 **Свой вопрос**\n\n"
            f"```python\n{code[:200]}{'...' if len(code) > 200 else ''}\n```\n\n"
            "Теперь напишите свой вопрос по этому коду:",
            parse_mode="Markdown"
        )
        await state.set_state(QuestionStates.waiting_for_followup)
        await callback.answer()
        return
    
    # For other categories, use template
    template = category_info["template"]
    if category_id == "code_translate":
        # Need additional info for translation
        await state.update_data(question_category=category_id, question_code=code)
        await callback.message.edit_text(
            "🔤 **Перевод кода**\n\n"
            f"```python\n{code[:200]}{'...' if len(code) > 200 else ''}\n```\n\n"
            "С какого языка перевести и на какой?\n"
            "Пример: 'с Python на JavaScript' или 'с JavaScript на Python'",
            parse_mode="Markdown"
        )
        await state.set_state(QuestionStates.waiting_for_followup)
        await callback.answer()
        return
    
    # Prepare question from template
    question = template.format(code=code)
    await state.update_data(question_text=question, question_category=category_id)
    
    # Send to OpenCode
    await process_question_with_opencode(callback.message, state, question, code, user_id)
    await callback.answer()


@router.message(QuestionStates.waiting_for_followup)
async def process_custom_question(message: types.Message, state: FSMContext):
    """Process custom question or translation details."""
    if not message.text:
        await message.answer("Пожалуйста, отправьте текст вопроса.")
        return
    
    user_id = message.from_user.id
    data = await state.get_data()
    code = data.get("question_code", "")
    category_id = data.get("question_category", "")
    
    if not code:
        await message.answer("❌ Код не найден. Пожалуйста, начните заново.")
        await state.clear()
        return
    
    # Handle translation
    if category_id == "code_translate":
        language_info = message.text.strip()
        template = QUESTION_CATEGORIES[category_id]["template"]
        question = template.format(from_lang="Python", to_lang="JavaScript", code=code)
        # Try to parse language info
        if "на" in language_info and "с" in language_info:
            # Extract languages from text like "с Python на JavaScript"
            parts = language_info.split()
            try:
                from_idx = parts.index("с")
                to_idx = parts.index("на")
                from_lang = parts[from_idx + 1] if from_idx + 1 < len(parts) else "Python"
                to_lang = parts[to_idx + 1] if to_idx + 1 < len(parts) else "JavaScript"
                question = template.format(from_lang=from_lang, to_lang=to_lang, code=code)
            except (ValueError, IndexError):
                pass
    else:
        # Custom question
        question = f"{message.text}\n\nКод:\n```python\n{code}\n```"
    
    await state.update_data(question_text=question)
    await process_question_with_opencode(message, state, question, code, user_id)


async def process_question_with_opencode(
    message: types.Message, 
    state: FSMContext,
    question: str,
    code: str,
    user_id: int
):
    """Send question to OpenCode and process response."""
    active_session = await session_manager.get_active_session(user_id)
    if active_session is None:
        await message.answer("❌ Сессия не найдена.")
        await state.clear()
        return

    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await message.answer("Session error: missing session ID.")
        await state.clear()
        return
    
    session_id = active_session['id']
    user_prefs = await session_manager.get_user_preference(user_id)
    provider_id = user_prefs.get("provider_id", "")
    model_id = user_prefs.get("model_id", "")
    
    # Get session folder for file tracking
    session_folder_path = session_files.get_session_folder(session_id)
    file_tracker = None
    try:
        file_tracker = FileChangeTracker(Path(session_folder_path))
        await file_tracker.take_before_snapshot()
        logger.debug(f"File tracking started for question session: {session_id}")
    except Exception as e:
        logger.warning(f"Failed to initialize file tracker: {e}")
    
    # Send status message
    status_message = await message.answer(
        f"🧠 **Анализирую вопрос...**\n\n"
        f"Используя: {provider_id}/{model_id}\n"
        "Пожалуйста, подождите...",
        parse_mode="Markdown"
    )
    
    # Collect thinking blocks
    thinking_messages = []
    last_thinking_sent = 0.0
    MIN_THINKING_INTERVAL = 0.3
    
    async def thinking_callback(thinking_text: str):
        """Callback for thinking blocks."""
        nonlocal last_thinking_sent
        
        if not thinking_text or len(thinking_text.strip()) == 0:
            return
        
        # Check if thinking display is enabled
        if not await session_manager.get_thinking_preference(user_id):
            return
        
        # Log thinking
        logger.info(f"Thinking: {thinking_text[:200]}...")
        
        # Split long thinking
        thinking_display = thinking_text.strip()
        max_length = 3500
        
        if len(thinking_display) <= max_length:
            parts = [thinking_display]
        else:
            # Simple split function
            parts = []
            while len(thinking_display) > max_length:
                split_at = max_length
                for separator in ['. ', '! ', '? ', '\n\n', '\n', ' ']:
                    pos = thinking_display.rfind(separator, 0, max_length)
                    if pos > 0 and pos > max_length * 0.7:
                        split_at = pos + len(separator)
                        break
                
                part = thinking_display[:split_at].strip()
                if part:
                    parts.append(part)
                thinking_display = thinking_display[split_at:].strip()
            
            if thinking_display:
                parts.append(thinking_display)
        
        for i, part in enumerate(parts):
            # Rate limiting
            current_time = time.time()
            if current_time - last_thinking_sent < MIN_THINKING_INTERVAL:
                continue
            
            prefix = "🤔 *Thinking*"
            if len(parts) > 1:
                prefix = f"🤔 *Thinking ({i+1}/{len(parts)})*"
            
            try:
                thinking_msg = await message.answer(f"{prefix}: {part}", parse_mode="Markdown")
                thinking_messages.append(thinking_msg.message_id)
                last_thinking_sent = current_time
            except Exception as e:
                logger.warning(f"Failed to send thinking message part {i+1}: {e}")
    
    try:
        # Call OpenCode
        result = await opencode_client.generate_code(
            prompt=question,
            language="python",
            session_id=session_id,
            provider_id=provider_id,
            model_id=model_id,
            thinking_callback=thinking_callback
        )
    except Exception as e:
        logger.error(f"Error processing question: {e}")
        try:
            await status_message.edit_text(
                f"❌ **Ошибка при обработке вопроса**\n\n"
                f"```\n{str(e)[:500]}\n```",
                parse_mode="Markdown"
            )
        except Exception as edit_error:
            logger.error(f"Failed to update error message: {edit_error}")
        await state.clear()
        return
    
    # Get file changes
    file_changes = {"created": [], "modified": [], "all": []}
    if file_tracker:
        try:
            file_changes = await file_tracker.take_after_snapshot()
            logger.info(f"File changes detected during question: {len(file_changes['all'])} files")
        except Exception as e:
            logger.error(f"Failed to get file changes: {e}")
    
    # Process result
    if isinstance(result, dict):
        response_text = result.get("response", "")
        files = result.get("files", {})
        session_folder = result.get("session_folder", "")
        error_flag = result.get("error", False)
        
        if error_flag:
            await status_message.edit_text(
                f"❌ **Ошибка OpenCode**\n\n```\n{response_text[:500]}\n```",
                parse_mode="Markdown"
            )
            await state.clear()
            return
    else:
        # Backward compatibility
        response_text = str(result) if result else "Нет ответа"
        files = {"created": [], "modified": [], "all": []}
        session_folder = ""
        logger.warning("Received string result instead of dict")
    
    # Update status message
    try:
        await status_message.delete()
    except:
        pass
    
    # Send response
    response_message = await message.answer(
        f"✅ **Ответ на вопрос**\n\n"
        f"{response_text[:3500]}{'...' if len(response_text) > 3500 else ''}",
        parse_mode="Markdown"
    )
    
    # Send files if any
    if files.get("all") and session_folder:
        try:
            await send_files_to_user(message, session_folder, files)
            
            # Add follow-up buttons
            followup_keyboard = await build_followup_questions_keyboard(session_id, files)
            if followup_keyboard:
                await message.answer(
                    "📋 **Что дальше?**\n\n"
                    "Выберите действие для продолжения:",
                    parse_mode="Markdown",
                    reply_markup=followup_keyboard
                )
        except Exception as e:
            logger.error(f"Failed to send files: {e}")
            await message.answer(f"⚠️ Файлы созданы, но не удалось отправить: {str(e)[:200]}")
    
    # Log thinking messages
    if thinking_messages:
        logger.info(f"Sent {len(thinking_messages)} thinking messages")
    
    await state.clear()


@router.callback_query(F.data.startswith("followup:"))
async def handle_followup_action(callback: CallbackQuery, state: FSMContext):
    """Handle follow-up actions after question."""
    if callback.message is None:
        await callback.answer()
        return
    
    action = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if not active_session:
        await callback.answer("❌ Нет активной сессии")
        return

    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await callback.answer("Session error: missing session ID.")
        return
    
    session_id = active_session['id']
    session_folder = session_files.get_session_folder(session_id)
    
    if action == "show_files":
        # List files in session
        files_list = session_files.list_session_files(session_id)
        if not files_list:
            text = "📁 В сессии пока нет файлов."
        else:
            text = f"📁 **Файлы в сессии {session_id[:8]}:**\n\n"
            for i, file_info in enumerate(files_list, 1):
                size_kb = file_info['size'] / 1024
                text += f"{i}. `{file_info['name']}` - {size_kb:.1f} KB\n"
        
        await callback.message.answer(text, parse_mode="Markdown")
        await callback.answer()
    
    elif action == "download_archive":
        # Create and send archive
        files_list = session_files.list_session_files(session_id)
        if not files_list:
            await callback.answer("❌ Нет файлов для архива")
            return
        
        file_paths = [file_info['name'] for file_info in files_list]
        session_path = Path(session_folder)
        
        # Create archive
        archive_buffer, archive_name, files_added = await ArchiveCreator.create_session_archive(
            session_path, file_paths
        )
        
        if not archive_buffer or files_added == 0:
            await callback.answer("❌ Не удалось создать архив")
            return
        
        # Send archive
        archive_size = ArchiveCreator.get_archive_size(archive_buffer)
        size_str = ArchiveCreator._format_size(archive_size)
        
        try:
            from aiogram.types import BufferedInputFile
            await callback.message.answer_document(
                BufferedInputFile(archive_buffer.getvalue(), filename=archive_name),
                caption=f"📦 Архив сессии: {archive_name}\n📁 Файлов: {files_added}\n📊 Размер: {size_str}"
            )
            await callback.answer("✅ Архив отправлен")
        except Exception as e:
            logger.error(f"Failed to send archive: {e}")
            await callback.answer("❌ Ошибка отправки архива")
    
    else:
        # Other follow-up actions require starting a new question
        await callback.answer(f"Используйте команду /ask для {action}")
    
    # Remove the follow-up buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass


@router.callback_query(F.data == "cancel_question")
async def handle_cancel_question(callback: CallbackQuery, state: FSMContext):
    """Cancel question process."""
    await state.clear()
    if callback.message:
        await callback.message.edit_text("❌ Вопрос отменён.")
    await callback.answer()

@router.callback_query(F.data == "question:start")
async def handle_question_start(callback: CallbackQuery, state: FSMContext):
    """Start question process from menu."""
    if callback.message is None:
        await callback.answer()
        return
    
    user_id = callback.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await callback.answer("❌ Вам нужна активная сессия. Используйте /newsession сначала.", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📝 **Задайте вопрос по коду**\n\n"
        "Отправьте мне код, а затем выберите тип вопроса.\n"
        "Вы можете:\n"
        "1. Отправить код в сообщении с ``` блоками\n"
        "2. Отправить .py файл\n"
        "3. Ответить на сообщение с кодом командой /ask\n\n"
        "Или используйте /cancel чтобы отменить.",
        parse_mode="Markdown"
    )
    
    await state.set_state(QuestionStates.waiting_for_question)
    await callback.answer()