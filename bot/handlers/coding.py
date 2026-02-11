from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ContentType, InlineKeyboardMarkup, InlineKeyboardButton
from typing import Dict, List
import logging
import asyncio
import time
from pathlib import Path

from core.session_manager import session_manager
from core.opencode_proxy import opencode_client
from core import session_files
from core.archive_utils import ArchiveCreator
from core.config import settings
from aiogram.types import FSInputFile, BufferedInputFile

router = Router()
logger = logging.getLogger("opencode_bot")

def split_text_into_parts(text, max_length=3500):
    """Split text into parts, trying to break at sentence boundaries."""
    parts = []
    while len(text) > max_length:
        # Find a good breaking point
        split_at = max_length
        # Try to break at sentence boundaries first
        for separator in ['. ', '! ', '? ', '\n\n', '\n', ' ']:
            pos = text.rfind(separator, 0, max_length)
            if pos > 0 and pos > max_length * 0.7:  # Use at least 70% of limit
                split_at = pos + len(separator)
                break
        
        part = text[:split_at].strip()
        if part:
            parts.append(part)
        text = text[split_at:].strip()
    
    if text:
        parts.append(text)
    return parts

async def send_files_to_user(message: Message, session_folder: Path, files: Dict[str, List[str]]) -> None:
    """Send files to user via Telegram."""
    if not files.get("all"):
        logger.debug("No files to send")
        return
    
    all_files = files["all"]
    session_folder = Path(session_folder)
    
    # Format file list for display
    file_list_message = ArchiveCreator.format_file_list_for_display(files, session_folder)
    if file_list_message:
        await message.answer(file_list_message, parse_mode="Markdown")
    
    # Send files based on count
    if len(all_files) <= settings.max_files_before_archive:
        # Send individual files
        await _send_individual_files(message, session_folder, all_files)
    else:
        # Send archive
        await _send_archive(message, session_folder, all_files)

async def _send_individual_files(message: Message, session_folder: Path, file_paths: List[str]) -> None:
    """Send individual files as Telegram documents."""
    files_to_send = await ArchiveCreator.create_individual_files_list(session_folder, file_paths)
    
    if not files_to_send:
        logger.warning("No files to send after filtering")
        return
    
    logger.info(f"Sending {len(files_to_send)} individual files to user {message.from_user.id}")
    
    for abs_path, rel_path in files_to_send:
        try:
            # Send as document with caption showing relative path
            await message.answer_document(
                FSInputFile(str(abs_path), filename=abs_path.name),
                caption=f"`{rel_path}`"
            )
            logger.debug(f"Sent file: {rel_path}")
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to send file {rel_path}: {e}")

async def _send_archive(message: Message, session_folder: Path, file_paths: List[str]) -> None:
    """Create and send ZIP archive of files."""
    logger.info(f"Creating archive for {len(file_paths)} files")
    
    archive_buffer, archive_name, files_added = await ArchiveCreator.create_session_archive(
        session_folder, file_paths
    )
    
    if not archive_buffer or files_added == 0:
        await message.answer("❌ Не удалось создать архив файлов.")
        return
    
    archive_size = ArchiveCreator.get_archive_size(archive_buffer)
    size_str = ArchiveCreator._format_size(archive_size)
    
    try:
        # Send archive as document
        await message.answer_document(
            BufferedInputFile(archive_buffer.getvalue(), filename=archive_name),
            caption=f"📦 Архив сессии: {archive_name}\n📁 Файлов: {files_added}\n📊 Размер: {size_str}"
        )
        logger.info(f"Sent archive '{archive_name}' with {files_added} files ({size_str})")
    except Exception as e:
        logger.error(f"Failed to send archive: {e}")
        await message.answer(f"❌ Ошибка при отправке архива: {str(e)[:200]}")

class GenerateStates(StatesGroup):
    waiting_for_prompt = State()

class DebugStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_error = State()

class RefactorStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_focus = State()

@router.message(Command("generate"))
async def cmd_generate(message: types.Message, state: FSMContext):
    logger.debug(f"INPUT: user_id={message.from_user.id}, chat_id={message.chat.id}, message_id={message.message_id}")
    logger.info(f"CMD_GENERATE: user={message.from_user.id}")
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("You need an active session. Use /newsession")
        return

    await message.answer("Describe the coding task you want me to solve:")
    await state.set_state(GenerateStates.waiting_for_prompt)

@router.message(GenerateStates.waiting_for_prompt)
async def process_generation_prompt(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    logger.debug(f"INPUT: user_id={user_id}, message_text='{message.text[:100]}', session_exists={active_session is not None}")
    logger.info(f"PROCESS_GENERATION_PROMPT: user={user_id}, prompt_length={len(message.text) if message.text else 0}")
    if active_session is None or not message.text:
        await message.answer("Session not found or invalid input.")
        await state.clear()
        return
    
    assert active_session is not None
    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await message.answer("Session error: missing session ID.")
        await state.clear()
        return
    session_id = active_session['id']
    prompt = message.text
    
    # Get user provider/model preferences
    user_prefs = await session_manager.get_user_preference(user_id)
    provider_id = user_prefs.get("provider_id", "")
    model_id = user_prefs.get("model_id", "")
    
    # Check if provider is connected
    providers_data = await opencode_client.get_providers()
    connected = providers_data.get("connected", [])
    
    if provider_id and provider_id not in connected:
        logger.warning(f"Provider {provider_id} not connected, connected providers: {connected}")
        # Reset to default provider from OpenCode
        default = await opencode_client.get_default_provider()
        provider_id = default["provider_id"]
        model_id = default["model_id"]
        await session_manager.set_user_preference(user_id, provider_id, model_id)
        
        # Show providers menu instead
        from bot.handlers.providers import build_providers_keyboard
        text, keyboard = await build_providers_keyboard(user_id)
        if keyboard:
            await message.answer(
                f"⚠️ Выбранный провайдер `{provider_id}` не подключен в OpenCode.\n\n"
                "Пожалуйста, выберите другого провайдера из списка ниже:",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await message.answer(
                f"⚠️ Выбранный провайдер `{provider_id}` не подключен в OpenCode.\n\n"
                "Используйте `/providers` чтобы увидеть доступные провайдеры.",
                parse_mode="Markdown"
            )
        await state.clear()
        return

    # Send initial message
    status_message = await message.answer(f"Generating code using {provider_id}/{model_id}... Please wait.")
    
    # Collect thinking blocks
    thinking_messages = []
    last_thinking_sent = 0.0
    MIN_THINKING_INTERVAL = 0.3  # seconds
    
    async def thinking_callback(thinking_text: str):
        """Callback to handle thinking blocks from OpenCode"""
        nonlocal last_thinking_sent
        
        logger.debug(f"thinking_callback invoked: {len(thinking_text)} chars, preview: {thinking_text[:100]}")
        
        if not thinking_text or len(thinking_text.strip()) == 0:
            logger.debug("thinking_callback: empty thinking text, returning")
            return
        
        # Check if thinking display is enabled
        if not await session_manager.get_thinking_preference(user_id):
            return
        
        # Log thinking to file
        logger.info(f"Thinking: {thinking_text[:200]}...")
        
        # Split long thinking into parts
        thinking_display = thinking_text.strip()
        max_length = 3500  # Leave room for prefix and numbering
        
        if len(thinking_display) <= max_length:
            parts = [thinking_display]
        else:
            parts = split_text_into_parts(thinking_display, max_length)
        
        for i, part in enumerate(parts):
            # Apply rate limiting for each part
            current_time = time.time()
            if current_time - last_thinking_sent < MIN_THINKING_INTERVAL:
                logger.debug(f"Skipping thinking part {i+1}/{len(parts)} due to rate limit")
                continue
            
            # Add part numbering if multiple parts
            if "?" in part:
                prefix = "❓ *Question*"
                if len(parts) > 1:
                    prefix = f"❓ *Question ({i+1}/{len(parts)})*"
            else:
                prefix = "🤔 *Thinking*"
                if len(parts) > 1:
                    prefix = f"🤔 *Thinking ({i+1}/{len(parts)})*"
            
            try:
                thinking_msg = await message.answer(f"{prefix}: {part}", parse_mode="Markdown")
                thinking_messages.append(thinking_msg.message_id)
                last_thinking_sent = current_time
                logger.debug(f"Sent thinking part {i+1}/{len(parts)}: {part[:100]}...")
            except Exception as e:
                logger.warning(f"Failed to send thinking message part {i+1}: {e}")
    
    # Call OpenCode Proxy with error handling
    try:
        result = await opencode_client.generate_code(prompt, "python", session_id, provider_id, model_id, thinking_callback)
    except Exception as e:
        logger.error(f"Error generating code: {e}")
        try:
            await status_message.edit_text(
                f"❌ *Error Generating Code*\n\n"
                f"```\n{str(e)[:500]}\n```\n\n"
                f"Please try again or use a different prompt.",
                parse_mode="Markdown"
            )
        except Exception as edit_error:
            logger.error(f"Failed to update error message: {edit_error}")
            await message.answer(f"❌ Error generating code: {str(e)[:200]}")
        await state.clear()
        return
    
    # Process result (now a dict with response and files)
    if isinstance(result, dict):
        response_text = result.get("response", "")
        files = result.get("files", {})
        session_folder = result.get("session_folder", "")
        error_flag = result.get("error", False)
        
        if error_flag:
            logger.error(f"OpenCode returned error: {response_text}")
            try:
                await status_message.edit_text(
                    f"❌ *Error Generating Code*\n\n"
                    f"```\n{response_text[:500]}\n```",
                    parse_mode="Markdown"
                )
            except Exception as edit_error:
                logger.error(f"Failed to update error message: {edit_error}")
                await message.answer(f"❌ Error generating code: {response_text[:200]}")
            await state.clear()
            return
    else:
        # Backward compatibility: result is a string
        response_text = str(result) if result else "No response received"
        files = {"created": [], "modified": [], "all": []}
        session_folder = ""
        logger.warning(f"Received string result instead of dict, using backward compatibility")
    
    # Send final result as new message (to appear after thinking blocks)
    try:
        # Try to delete status message to avoid confusion
        try:
            await status_message.delete()
            logger.debug(f"Status message deleted")
        except Exception as delete_error:
            logger.debug(f"Could not delete status message: {delete_error}, leaving it as is")
        
        # Send final result as new message
        logger.debug(f"Sending final result as new message, result_length={len(response_text)}")
        final_message = await message.answer(f"✅ *Code Generated*\n\n```python\n{response_text}\n```", parse_mode="Markdown")
        logger.debug(f"Final result sent as new message with message_id={final_message.message_id}")
    except Exception as e:
        logger.error(f"Failed to send final result: {e}")
        # Fallback: edit status message
        try:
            await status_message.edit_text(f"✅ *Code Generated*\n\n```python\n{response_text[:1000]}\n```", parse_mode="Markdown")
            logger.debug(f"Fallback: edited status message")
        except Exception as edit_error:
            logger.error(f"Failed to update status message: {edit_error}")
    
    # Send created/modified files to user
    if files.get("all") and session_folder:
        try:
            await send_files_to_user(message, session_folder, files)
        except Exception as e:
            logger.error(f"Failed to send files to user: {e}")
            await message.answer(f"⚠️ Файлы созданы, но не удалось отправить: {str(e)[:200]}")
    
    # Log thinking messages count
    if thinking_messages:
        logger.info(f"Sent {len(thinking_messages)} thinking messages to user")
    
    await state.clear()

async def extract_code_from_message(message: Message) -> str:
    """Extract code from various message types: text, document, reply."""
    code = ""
    
    # Check for document (file upload)
    if message.document:
        # Only accept text files for now
        if message.document.mime_type and 'text' in message.document.mime_type:
            try:
                assert message.bot is not None
                file = await message.bot.get_file(message.document.file_id)
                if not file.file_path:
                    return ""
                file_bytes = await message.bot.download_file(file.file_path)
                assert file_bytes is not None
                code = file_bytes.read().decode('utf-8')
            except Exception as e:
                logger.error(f"Error reading document: {e}")
                return ""
        else:
            return ""
    
    # Check for text message
    elif message.text:
        text = message.text
        
        # Check for code blocks in markdown (```python ... ```)
        if '```' in text:
            # Extract code between first ``` and last ```
            parts = text.split('```')
            if len(parts) >= 3:
                # Get the middle part (skip language specifier if present)
                code_block = parts[1]
                # Remove language specifier (e.g., "python\n")
                lines = code_block.split('\n', 1)
                if len(lines) > 1:
                    code = lines[1]
                else:
                    code = code_block
        else:
            # Use entire text as code
            code = text
    
    # Check for reply to a message with code
    elif message.reply_to_message:
        return await extract_code_from_message(message.reply_to_message)
    
    return code

@router.message(Command("debug"))
async def cmd_debug(message: Message, state: FSMContext):
    logger.info(f"cmd_debug called by user {message.from_user.id}")
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("You need an active session. Use /newsession")
        return
    
    # Check if message contains code or is a reply to code
    code = await extract_code_from_message(message)
    if code:
        # Store code in state and ask for error description
        await state.update_data(debug_code=code)
        await message.answer("Code received. Now describe the error or issue you're facing:")
        await state.set_state(DebugStates.waiting_for_error)
    else:
        # No code found, ask user to send code
        await message.answer("Please send me the code to debug. You can:\n1. Send a .py file\n2. Send code in a message with ``` code blocks\n3. Reply to a message containing code with /debug")
        await state.set_state(DebugStates.waiting_for_code)

@router.message(DebugStates.waiting_for_code)
async def process_debug_code(message: Message, state: FSMContext):
    logger.info(f"process_debug_code called by user {message.from_user.id}")
    code = await extract_code_from_message(message)
    
    if not code:
        await message.answer("Could not extract code from your message. Please try again.")
        return
    
    await state.update_data(debug_code=code)
    await message.answer("Code received. Now describe the error or issue you're facing:")
    await state.set_state(DebugStates.waiting_for_error)

@router.message(DebugStates.waiting_for_error)
async def process_debug_error(message: Message, state: FSMContext):
    logger.info(f"process_debug_error called by user {message.from_user.id}")
    if not message.text:
        await message.answer("Please describe the error in text.")
        return
    
    error_desc = message.text
    data = await state.get_data()
    code = data.get("debug_code", "")
    
    if not code:
        await message.answer("Code not found. Please start over with /debug")
        await state.clear()
        return
    
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    if active_session is None:
        await message.answer("Session expired. Use /newsession")
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
    
    # Send initial message
    status_message = await message.answer(f"Debugging code using {provider_id}/{model_id}... Please wait.")
    
    # Collect thinking blocks
    thinking_messages = []
    last_thinking_sent = 0.0
    MIN_THINKING_INTERVAL = 0.3  # seconds
    
    async def thinking_callback(thinking_text: str):
        """Callback to handle thinking blocks from OpenCode"""
        nonlocal last_thinking_sent
        
        logger.debug(f"thinking_callback invoked: {len(thinking_text)} chars, preview: {thinking_text[:100]}")
        
        if not thinking_text or len(thinking_text.strip()) == 0:
            logger.debug("thinking_callback: empty thinking text, returning")
            return
        
        # Check if thinking display is enabled
        if not await session_manager.get_thinking_preference(user_id):
            return
        
        # Log thinking to file
        logger.info(f"Thinking: {thinking_text[:200]}...")
        
        # Split long thinking into parts
        thinking_display = thinking_text.strip()
        max_length = 3500  # Leave room for prefix and numbering
        
        if len(thinking_display) <= max_length:
            parts = [thinking_display]
        else:
            parts = split_text_into_parts(thinking_display, max_length)
        
        for i, part in enumerate(parts):
            # Apply rate limiting for each part
            current_time = time.time()
            if current_time - last_thinking_sent < MIN_THINKING_INTERVAL:
                logger.debug(f"Skipping thinking part {i+1}/{len(parts)} due to rate limit")
                continue
            
            # Add part numbering if multiple parts
            if "?" in part:
                prefix = "❓ *Question*"
                if len(parts) > 1:
                    prefix = f"❓ *Question ({i+1}/{len(parts)})*"
            else:
                prefix = "🤔 *Thinking*"
                if len(parts) > 1:
                    prefix = f"🤔 *Thinking ({i+1}/{len(parts)})*"
            
            try:
                thinking_msg = await message.answer(f"{prefix}: {part}", parse_mode="Markdown")
                thinking_messages.append(thinking_msg.message_id)
                last_thinking_sent = current_time
                logger.debug(f"Sent thinking part {i+1}/{len(parts)}: {part[:100]}...")
            except Exception as e:
                logger.warning(f"Failed to send thinking message part {i+1}: {e}")
    
    # Call OpenCode Proxy with error handling
    try:
        result = await opencode_client.debug_code(code, error_desc, session_id, provider_id, model_id, thinking_callback)
    except Exception as e:
        logger.error(f"Error debugging code: {e}")
        try:
            await status_message.edit_text(
                f"❌ *Error Debugging Code*\n\n"
                f"```\n{str(e)[:500]}\n```\n\n"
                f"Please try again or check your code and error description.",
                parse_mode="Markdown"
            )
        except Exception as edit_error:
            logger.error(f"Failed to update error message: {edit_error}")
            await message.answer(f"❌ Error debugging code: {str(e)[:200]}")
        await state.clear()
        return
    
    # Process result (now a dict with response and files)
    if isinstance(result, dict):
        response_text = result.get("response", "")
        files = result.get("files", {})
        session_folder = result.get("session_folder", "")
        error_flag = result.get("error", False)
        
        if error_flag:
            logger.error(f"OpenCode returned error during debugging: {response_text}")
            try:
                await status_message.edit_text(
                    f"❌ *Error Debugging Code*\n\n"
                    f"```\n{response_text[:500]}\n```",
                    parse_mode="Markdown"
                )
            except Exception as edit_error:
                logger.error(f"Failed to update error message: {edit_error}")
                await message.answer(f"❌ Error debugging code: {response_text[:200]}")
            await state.clear()
            return
    else:
        # Backward compatibility: result is a string
        response_text = str(result) if result else "No response received"
        files = {"created": [], "modified": [], "all": []}
        session_folder = ""
        logger.warning(f"Received string result instead of dict, using backward compatibility")
    
    # Send final result as new message (to appear after thinking blocks)
    try:
        # Try to delete status message to avoid confusion
        try:
            await status_message.delete()
            logger.debug(f"Status message deleted")
        except Exception as delete_error:
            logger.debug(f"Could not delete status message: {delete_error}, leaving it as is")
        
        # Send final result as new message
        logger.debug(f"Sending final debug result as new message, result_length={len(response_text)}")
        final_message = await message.answer(f"✅ *Debug Result*\n\n```python\n{response_text}\n```", parse_mode="Markdown")
        logger.debug(f"Final debug result sent as new message with message_id={final_message.message_id}")
    except Exception as e:
        logger.error(f"Failed to send final debug result: {e}")
        # Fallback: edit status message
        try:
            await status_message.edit_text(f"✅ *Debug Result*\n\n```python\n{response_text[:1000]}\n```", parse_mode="Markdown")
            logger.debug(f"Fallback: edited status message")
        except Exception as edit_error:
            logger.error(f"Failed to update status message: {edit_error}")
    
    # Send created/modified files to user
    if files.get("all") and session_folder:
        try:
            await send_files_to_user(message, session_folder, files)
        except Exception as e:
            logger.error(f"Failed to send files to user: {e}")
            await message.answer(f"⚠️ Файлы созданы, но не удалось отправить: {str(e)[:200]}")
    
    # Log thinking messages count
    if thinking_messages:
        logger.info(f"Sent {len(thinking_messages)} thinking messages to user during debugging")
    
    await state.clear()

@router.message(Command("refactor"))
async def cmd_refactor(message: Message, state: FSMContext, command: CommandObject):
    logger.info(f"cmd_refactor called by user {message.from_user.id}")
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("You need an active session. Use /newsession")
        return
    
    # Check for focus area in command arguments
    focus = command.args if command and command.args else ""
    
    # Check if message contains code or is a reply to code
    code = await extract_code_from_message(message)
    if code:
        if focus:
            # We have both code and focus, proceed directly
            await state.update_data(refactor_code=code, refactor_focus=focus)
            await process_refactor_code(message, state)
        else:
            # Store code and ask for focus area
            await state.update_data(refactor_code=code)
            await message.answer("Code received. What aspect should I focus on? (e.g., 'performance', 'readability', 'add comments'):")
            await state.set_state(RefactorStates.waiting_for_focus)
    else:
        # No code found, ask user to send code
        if focus:
            await state.update_data(refactor_focus=focus)
        await message.answer("Please send me the code to refactor. You can:\n1. Send a .py file\n2. Send code in a message with ``` code blocks\n3. Reply to a message containing code with /refactor <focus>")
        await state.set_state(RefactorStates.waiting_for_code)

@router.message(RefactorStates.waiting_for_code)
async def process_refactor_code_input(message: Message, state: FSMContext):
    logger.info(f"process_refactor_code_input called by user {message.from_user.id}")
    code = await extract_code_from_message(message)
    
    if not code:
        await message.answer("Could not extract code from your message. Please try again.")
        return
    
    await state.update_data(refactor_code=code)
    
    data = await state.get_data()
    if data.get("refactor_focus"):
        # We already have focus, proceed to refactor
        await process_refactor_code(message, state)
    else:
        # Ask for focus
        await message.answer("Code received. What aspect should I focus on? (e.g., 'performance', 'readability', 'add comments'):")
        await state.set_state(RefactorStates.waiting_for_focus)

@router.message(RefactorStates.waiting_for_focus)
async def process_refactor_focus(message: Message, state: FSMContext):
    logger.info(f"process_refactor_focus called by user {message.from_user.id}")
    if not message.text:
        await message.answer("Please describe the refactoring focus in text.")
        return
    
    focus = message.text
    await state.update_data(refactor_focus=focus)
    await process_refactor_code(message, state)

async def process_refactor_code(message: Message, state: FSMContext):
    data = await state.get_data()
    code = data.get("refactor_code", "")
    focus = data.get("refactor_focus", "general improvements")
    
    if not code:
        await message.answer("Code not found. Please start over with /refactor")
        await state.clear()
        return
    
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    if active_session is None:
        await message.answer("Session expired. Use /newsession")
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

    # Send initial message
    status_message = await message.answer(f"Refactoring code using {provider_id}/{model_id}... Please wait.")
    
    # Collect thinking blocks
    thinking_messages = []
    last_thinking_sent = 0.0
    MIN_THINKING_INTERVAL = 0.3  # seconds
    
    async def thinking_callback(thinking_text: str):
        """Callback to handle thinking blocks from OpenCode"""
        nonlocal last_thinking_sent
        
        logger.debug(f"thinking_callback invoked: {len(thinking_text)} chars, preview: {thinking_text[:100]}")
        
        if not thinking_text or len(thinking_text.strip()) == 0:
            logger.debug("thinking_callback: empty thinking text, returning")
            return
        
        # Check if thinking display is enabled
        if not await session_manager.get_thinking_preference(user_id):
            return
        
        # Log thinking to file
        logger.info(f"Thinking: {thinking_text[:200]}...")
        
        # Split long thinking into parts
        thinking_display = thinking_text.strip()
        max_length = 3500  # Leave room for prefix and numbering
        
        if len(thinking_display) <= max_length:
            parts = [thinking_display]
        else:
            parts = split_text_into_parts(thinking_display, max_length)
        
        for i, part in enumerate(parts):
            # Apply rate limiting for each part
            current_time = time.time()
            if current_time - last_thinking_sent < MIN_THINKING_INTERVAL:
                logger.debug(f"Skipping thinking part {i+1}/{len(parts)} due to rate limit")
                continue
            
            # Add part numbering if multiple parts
            if "?" in part:
                prefix = "❓ *Question*"
                if len(parts) > 1:
                    prefix = f"❓ *Question ({i+1}/{len(parts)})*"
            else:
                prefix = "🤔 *Thinking*"
                if len(parts) > 1:
                    prefix = f"🤔 *Thinking ({i+1}/{len(parts)})*"
            
            try:
                thinking_msg = await message.answer(f"{prefix}: {part}", parse_mode="Markdown")
                thinking_messages.append(thinking_msg.message_id)
                last_thinking_sent = current_time
                logger.debug(f"Sent thinking part {i+1}/{len(parts)}: {part[:100]}...")
            except Exception as e:
                logger.warning(f"Failed to send thinking message part {i+1}: {e}")
    
    # Call OpenCode Proxy with error handling
    try:
        result = await opencode_client.refactor_code(code, focus, session_id, provider_id, model_id, thinking_callback)
    except Exception as e:
        logger.error(f"Error refactoring code: {e}")
        try:
            await status_message.edit_text(
                f"❌ *Error Refactoring Code*\n\n"
                f"```\n{str(e)[:500]}\n```\n\n"
                f"Please try again or check your code and focus description.",
                parse_mode="Markdown"
            )
        except Exception as edit_error:
            logger.error(f"Failed to update error message: {edit_error}")
            await message.answer(f"❌ Error refactoring code: {str(e)[:200]}")
        await state.clear()
        return
    
    # Process result (now a dict with response and files)
    if isinstance(result, dict):
        response_text = result.get("response", "")
        files = result.get("files", {})
        session_folder = result.get("session_folder", "")
        error_flag = result.get("error", False)
        
        if error_flag:
            logger.error(f"OpenCode returned error during refactoring: {response_text}")
            try:
                await status_message.edit_text(
                    f"❌ *Error Refactoring Code*\n\n"
                    f"```\n{response_text[:500]}\n```",
                    parse_mode="Markdown"
                )
            except Exception as edit_error:
                logger.error(f"Failed to update error message: {edit_error}")
                await message.answer(f"❌ Error refactoring code: {response_text[:200]}")
            await state.clear()
            return
    else:
        # Backward compatibility: result is a string
        response_text = str(result) if result else "No response received"
        files = {"created": [], "modified": [], "all": []}
        session_folder = ""
        logger.warning(f"Received string result instead of dict, using backward compatibility")
    
    # Send final result as new message (to appear after thinking blocks)
    try:
        # Try to delete status message to avoid confusion
        try:
            await status_message.delete()
            logger.debug(f"Status message deleted")
        except Exception as delete_error:
            logger.debug(f"Could not delete status message: {delete_error}, leaving it as is")
        
        # Send final result as new message
        logger.debug(f"Sending final refactored result as new message, result_length={len(response_text)}")
        final_message = await message.answer(f"✅ *Refactored Code*\n\n```python\n{response_text}\n```", parse_mode="Markdown")
        logger.debug(f"Final refactored result sent as new message with message_id={final_message.message_id}")
    except Exception as e:
        logger.error(f"Failed to send final refactored result: {e}")
        # Fallback: edit status message
        try:
            await status_message.edit_text(f"✅ *Refactored Code*\n\n```python\n{response_text[:1000]}\n```", parse_mode="Markdown")
            logger.debug(f"Fallback: edited status message")
        except Exception as edit_error:
            logger.error(f"Failed to update status message: {edit_error}")
    
    # Send created/modified files to user
    if files.get("all") and session_folder:
        try:
            await send_files_to_user(message, session_folder, files)
        except Exception as e:
            logger.error(f"Failed to send files to user: {e}")
            await message.answer(f"⚠️ Файлы созданы, но не удалось отправить: {str(e)[:200]}")
    
    # Log thinking messages count
    if thinking_messages:
        logger.info(f"Sent {len(thinking_messages)} thinking messages to user during refactoring")
    
    await state.clear()

@router.message(Command("settings"))
async def cmd_settings(message: types.Message):
    """Show settings menu for thinking display"""
    user_id = message.from_user.id
    show_thinking = await session_manager.get_thinking_preference(user_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Thinking Display ON" if show_thinking else "❌ Thinking Display OFF",
                callback_data="toggle_thinking"
            )
        ],
        [
            InlineKeyboardButton(
                text="📤 Publish Session to GitHub",
                callback_data="publish_session"
            )
        ]
    ])
    
    status_text = "ON" if show_thinking else "OFF"
    active_session = await session_manager.get_active_session(user_id)
    session_id_short = active_session['id'][:8] if active_session else 'None'
    await message.answer(
        f"⚙️ *Settings*\n\n"
        f"• Thinking Display: {status_text}\n\n"
        f"• Active session: {session_id_short}\n\n"
        f"Use buttons below to manage settings.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

@router.callback_query(lambda c: c.data == "toggle_thinking")
async def toggle_thinking_callback(callback_query: types.CallbackQuery):
    """Toggle thinking display preference"""
    user_id = callback_query.from_user.id
    current = await session_manager.get_thinking_preference(user_id)
    new_setting = not current
    await session_manager.set_thinking_preference(user_id, new_setting)
    
    # Update button text
    active_session = await session_manager.get_active_session(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Thinking Display ON" if new_setting else "❌ Thinking Display OFF",
                callback_data="toggle_thinking"
            )
        ],
        [
            InlineKeyboardButton(
                text="📤 Publish Session to GitHub",
                callback_data="publish_session"
            )
        ]
    ])
    
    status_text = "ON" if new_setting else "OFF"
    session_id_short = active_session['id'][:8] if active_session else 'None'
    await callback_query.message.edit_text(
        f"⚙️ *Settings*\n\n"
        f"• Thinking Display: {status_text}\n\n"
        f"• Active session: {session_id_short}\n\n"
        f"Use buttons below to manage settings.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer(f"Thinking display turned {status_text}")

@router.callback_query(lambda c: c.data == "publish_session")
async def publish_session_callback(callback_query: types.CallbackQuery):
    """Publish current session to GitHub from settings"""
    user_id = callback_query.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await callback_query.answer("You need an active session. Use /newsession first.", show_alert=True)
        return

    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await callback_query.answer("Session error: missing session ID.", show_alert=True)
        return
    
    session_id = active_session['id']
    
    # Send initial message
    await callback_query.answer("Starting publish process...")
    
    # Edit original message to show progress
    await callback_query.message.edit_text(
        f"⚙️ *Settings*\n\n"
        f"• Publishing session {session_id[:8]}...\n\n"
        f"Please wait, this may take a moment.",
        parse_mode="Markdown"
    )
    
    # Publish to GitHub
    result = session_files.publish_to_github(session_id)
    
    if result.get("success"):
        files = result.get("files_copied", [])
        # Update settings message with success
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Thinking Display ON" if await session_manager.get_thinking_preference(user_id) else "❌ Thinking Display OFF",
                    callback_data="toggle_thinking"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 Publish Session to GitHub",
                    callback_data="publish_session"
                )
            ]
        ])
        
        await callback_query.message.edit_text(
            f"⚙️ *Settings*\n\n"
            f"✅ Published session to GitHub!\n\n"
            f"• Session: {session_id[:8]}\n"
            f"• Files: {len(files)}\n"
            f"• Repo: https://github.com/CAMOPKAH/klavdii_work_place\n\n"
            f"Use buttons below to manage settings.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        await callback_query.answer("Published successfully!", show_alert=False)
    else:
        error = result.get("error", "Unknown error")
        await callback_query.message.edit_text(
            f"⚙️ *Settings*\n\n"
            f"❌ Failed to publish to GitHub:\n\n```\n{error[:500]}\n```\n\n"
            f"Use buttons below to try again.",
            parse_mode="Markdown",
            reply_markup=callback_query.message.reply_markup  # Keep original keyboard
        )
        await callback_query.answer("Publish failed", show_alert=True)

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    """Send welcome message"""
    await message.answer("Hello Klavdii is work!")

@router.message(Command("version"))
async def cmd_version(message: types.Message):
    """Show bot version"""
    version_file = Path("VERSION")
    if version_file.exists():
        version = version_file.read_text().strip()
    else:
        version = "unknown"
    
    await message.answer(f"Klavdii Bot Version: {version}")

@router.message(Command("publish"))
async def cmd_publish(message: types.Message):
    """Publish current session files to GitHub"""
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("You need an active session. Use /newsession first.")
        return

    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await message.answer("Session error: missing session ID.")
        return
    
    session_id = active_session['id']
    
    # Send initial message
    status_msg = await message.answer(f"📤 Publishing session {session_id[:8]}... Please wait.")
    
    # Publish to GitHub
    result = session_files.publish_to_github(session_id)
    
    if result.get("success"):
        files = result.get("files_copied", [])
        await status_msg.edit_text(
            f"✅ Published session to GitHub!\n\n"
            f"• Session: {session_id[:8]}\n"
            f"• Files: {len(files)}\n"
            f"• Repo: https://github.com/CAMOPKAH/klavdii_work_place\n\n"
            f"Files published:\n" + "\n".join(f"  - {f}" for f in files[:10]) +
            ("\n  ..." if len(files) > 10 else ""),
            parse_mode="Markdown"
        )
    else:
        error = result.get("error", "Unknown error")
        await status_msg.edit_text(
            f"❌ Failed to publish to GitHub:\n\n```\n{error[:500]}\n```",
            parse_mode="Markdown"
        )

@router.message(lambda message: message.text and not message.text.startswith('/'))
async def handle_text_message(message: types.Message, state: FSMContext):
    """Handle regular text messages as code generation requests"""
    # Check if we're in a state (waiting for input)
    current_state = await state.get_state()
    if current_state is not None:
        # Let other handlers process
        return
    
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("You need an active session to generate code. Use /newsession first.")
        return

    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await message.answer("Session error: missing session ID.")
        return
    
    # Get user preferences
    user_prefs = await session_manager.get_user_preference(user_id)
    provider_id = user_prefs.get("provider_id", "")
    model_id = user_prefs.get("model_id", "")
    
    prompt = message.text
    session_id = active_session['id']
    
    # Send initial message
    status_message = await message.answer(f"Generating code from your request using {provider_id}/{model_id}... Please wait.")
    
    # Collect thinking blocks
    thinking_messages = []
    last_thinking_sent = 0.0
    MIN_THINKING_INTERVAL = 0.3  # seconds
    
    async def thinking_callback(thinking_text: str):
        """Callback to handle thinking blocks from OpenCode"""
        nonlocal last_thinking_sent
        
        logger.debug(f"thinking_callback invoked: {len(thinking_text)} chars, preview: {thinking_text[:100]}")
        
        if not thinking_text or len(thinking_text.strip()) == 0:
            logger.debug("thinking_callback: empty thinking text, returning")
            return
        
        # Check if thinking display is enabled
        if not await session_manager.get_thinking_preference(user_id):
            logger.debug("thinking_callback: thinking display disabled for user")
            return
        
        # Log thinking to file
        logger.info(f"Thinking: {thinking_text[:200]}...")
        
        # Split long thinking into parts
        thinking_display = thinking_text.strip()
        max_length = 3500  # Leave room for prefix and numbering
        
        if len(thinking_display) <= max_length:
            parts = [thinking_display]
        else:
            parts = split_text_into_parts(thinking_display, max_length)
        
        for i, part in enumerate(parts):
            # Apply rate limiting for each part
            current_time = time.time()
            if current_time - last_thinking_sent < MIN_THINKING_INTERVAL:
                logger.debug(f"Skipping thinking part {i+1}/{len(parts)} due to rate limit")
                continue
            
            # Add part numbering if multiple parts
            if "?" in part:
                prefix = "❓ *Question*"
                if len(parts) > 1:
                    prefix = f"❓ *Question ({i+1}/{len(parts)})*"
            else:
                prefix = "🤔 *Thinking*"
                if len(parts) > 1:
                    prefix = f"🤔 *Thinking ({i+1}/{len(parts)})*"
            
            try:
                thinking_msg = await message.answer(f"{prefix}: {part}", parse_mode="Markdown")
                thinking_messages.append(thinking_msg.message_id)
                last_thinking_sent = current_time
                logger.debug(f"Sent thinking part {i+1}/{len(parts)}: {part[:100]}...")
            except Exception as e:
                logger.warning(f"Failed to send thinking message part {i+1}: {e}")
    
    # Call OpenCode Proxy with error handling
    try:
        result = await opencode_client.generate_code(prompt, "python", session_id, provider_id, model_id, thinking_callback)
    except Exception as e:
        logger.error(f"Error generating code: {e}")
        try:
            await status_message.edit_text(
                f"❌ *Error Generating Code*\n\n"
                f"```\n{str(e)[:500]}\n```\n\n"
                f"Please try again or use a different prompt.",
                parse_mode="Markdown"
            )
        except Exception as edit_error:
            logger.error(f"Failed to update error message: {edit_error}")
            await message.answer(f"❌ Error generating code: {str(e)[:200]}")
        return
    
    # Process result (now a dict with response and files)
    if isinstance(result, dict):
        response_text = result.get("response", "")
        files = result.get("files", {})
        session_folder = result.get("session_folder", "")
        error_flag = result.get("error", False)
        
        if error_flag:
            logger.error(f"OpenCode returned error: {response_text}")
            try:
                await status_message.edit_text(
                    f"❌ *Error Generating Code*\n\n"
                    f"```\n{response_text[:500]}\n```",
                    parse_mode="Markdown"
                )
            except Exception as edit_error:
                logger.error(f"Failed to update error message: {edit_error}")
                await message.answer(f"❌ Error generating code: {response_text[:200]}")
            return
    else:
        # Backward compatibility: result is a string
        response_text = str(result) if result else "No response received"
        files = {"created": [], "modified": [], "all": []}
        session_folder = ""
        logger.warning(f"Received string result instead of dict, using backward compatibility")
    
    # Send final result as new message (to appear after thinking blocks)
    try:
        # Try to delete status message to avoid confusion
        try:
            await status_message.delete()
            logger.debug(f"Status message deleted")
        except Exception as delete_error:
            logger.debug(f"Could not delete status message: {delete_error}, leaving it as is")
        
        # Send final result as new message
        logger.debug(f"Sending final result as new message, result_length={len(response_text)}")
        final_message = await message.answer(f"✅ *Code Generated*\n\n```python\n{response_text}\n```", parse_mode="Markdown")
        logger.debug(f"Final result sent as new message with message_id={final_message.message_id}")
    except Exception as e:
        logger.error(f"Failed to send final result: {e}")
        # Fallback: edit status message
        try:
            await status_message.edit_text(f"✅ *Code Generated*\n\n```python\n{response_text[:1000]}\n```", parse_mode="Markdown")
            logger.debug(f"Fallback: edited status message")
        except Exception as edit_error:
            logger.error(f"Failed to update status message: {edit_error}")
    
    # Send created/modified files to user
    if files.get("all") and session_folder:
        try:
            await send_files_to_user(message, session_folder, files)
        except Exception as e:
            logger.error(f"Failed to send files to user: {e}")
            await message.answer(f"⚠️ Файлы созданы, но не удалось отправить: {str(e)[:200]}")
    
    # Log thinking messages count
    if thinking_messages:
        logger.info(f"Sent {len(thinking_messages)} thinking messages to user")
