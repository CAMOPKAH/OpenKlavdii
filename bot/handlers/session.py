from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from core.session_manager import session_manager
from core import session_files
from core.archive_utils import ArchiveCreator
from core.config import settings
from aiogram.types import FSInputFile, BufferedInputFile
import logging
import asyncio
from pathlib import Path

router = Router()
logger = logging.getLogger("opencode_balls")

async def _send_individual_files(message: types.Message, session_folder: Path, file_paths: list) -> None:
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

async def _send_archive(message: types.Message, session_folder: Path, file_paths: list) -> None:
    """Create and send ZIP archive of files."""
    logger.info(f"Creating archive for {len(file_paths)} files")
    
    archive_buffer, archive_name, files_added = await ArchiveCreator.create_session_archive(
        session_folder, file_paths
    )
    
    if not archive_buffer or files_added == 0:
        await message.answer("❌ Failed to create file archive.")
        return
    
    # Send archive
    try:
        await message.answer_document(
            BufferedInputFile(archive_buffer.getvalue(), filename=archive_name),
            caption=f"📦 Archive: {archive_name} ({files_added} files)"
        )
        logger.info(f"Sent archive {archive_name} with {files_added} files")
    except Exception as e:
        logger.error(f"Failed to send archive: {e}")
        await message.answer("❌ Failed to send archive file.")

async def send_session_files(message: types.Message, session_folder: Path, all_files: list) -> None:
    """Send session files to user via Telegram."""
    if not all_files:
        await message.answer("No files to download.")
        return
    
    # Format file list for display
    file_list_message = ArchiveCreator.format_file_list_for_display(
        {"all": all_files, "created": all_files, "modified": []}, 
        session_folder
    )
    if file_list_message:
        await message.answer(file_list_message, parse_mode="Markdown")
    
    # Send files based on count
    if len(all_files) <= settings.max_files_before_archive:
        # Send individual files
        await _send_individual_files(message, session_folder, all_files)
    else:
        # Send archive
        await _send_archive(message, session_folder, all_files)

class EditFileStates(StatesGroup):
    waiting_for_filename = State()
    waiting_for_content = State()

@router.message(Command("newsession"))
async def cmd_new_session(message: types.Message):
    logger.info(f"cmd_new_session called by user {message.from_user.id}")
    user_id = message.from_user.id
    session_id = await session_manager.create_session(user_id)
    
    # Get session folder path
    session_folder = await session_manager.get_session_folder(user_id)
    folder_path = str(session_folder) if session_folder else f"work_place/{session_id}"
    
    await message.answer(
        f"✅ Created new session!\n"
        f"<b>Session ID:</b> <code>{session_id}</code>\n"
        f"<b>Folder:</b> <code>{folder_path}</code>\n\n"
        f"All files created by OpenCode will be saved in this folder.\n"
        f"Use /publish to share files on GitHub.",
        parse_mode="HTML"
    )

@router.message(Command("listsessions"))
async def cmd_list_sessions(message: types.Message):
    logger.info(f"cmd_list_sessions called by user {message.from_user.id}, text: {message.text}")
    user_id = message.from_user.id
    sessions = await session_manager.list_user_sessions(user_id)
    
    if not sessions:
        await message.answer("No active sessions found. Start one with /newsession")
        return

    text = "<b>📋 Your Sessions:</b>\n"
    active_session_data = await session_manager.get_active_session(user_id)
    active_id = None
    if active_session_data:
        active_id = active_session_data['id']

    for s in sessions:
        status = "🟢 (Active)" if s['id'] == active_id else ""
        text += f"- <code>{s['id']}</code> {status}\n  Created: {s['created_at']}\n"
    
    await message.answer(text, parse_mode="HTML")

@router.message(Command("switchsession"))
async def cmd_switch_session(message: types.Message):
    if not message.text:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Usage: /switchsession <session_id>")
        return
    
    session_id = args[1]
    user_id = message.from_user.id
    success = await session_manager.switch_session(user_id, session_id)
    
    if success:
        await message.answer(f"🔄 Switched to session: <code>{session_id}</code>", parse_mode="HTML")
    else:
        await message.answer("❌ Session not found.", parse_mode="HTML")

@router.message(Command("files"))
async def cmd_list_files(message: types.Message):
    """List files in current session folder"""
    logger.info(f"cmd_list_files called by user {message.from_user.id}")
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
    files = session_files.list_session_files(session_id)
    
    if not files:
        await message.answer("No files in session folder yet.")
        return
    
    text = f"<b>📁 Files in session {session_id[:8]}:</b>\n\n"
    for i, file_info in enumerate(files, 1):
        size_kb = file_info['size'] / 1024
        text += f"{i}. <code>{file_info['name']}</code>\n"
        text += f"   Size: {size_kb:.1f} KB\n"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("download"))
async def cmd_download_files(message: types.Message):
    """Download all files from current session"""
    logger.info(f"cmd_download_files called by user {message.from_user.id}")
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
    files_info = session_files.list_session_files(session_id)
    
    if not files_info:
        await message.answer("No files in session folder to download.")
        return
    
    # Extract just filenames
    file_names = [file_info['name'] for file_info in files_info]
    
    # Get session folder
    session_folder = session_files.get_session_folder(session_id)
    
    # Send initial message
    status_msg = await message.answer(f"📥 Preparing to download {len(file_names)} files...")
    
    # Send files
    await send_session_files(message, session_folder, file_names)
    
    # Update status
    await status_msg.edit_text(f"✅ Downloaded {len(file_names)} files from session {session_id[:8]}")


@router.message(Command("view"))
async def cmd_view_file(message: types.Message, command: CommandObject):
    """View content of a file in current session"""
    logger.info(f"cmd_view_file called by user {message.from_user.id}")
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("You need an active session. Use /newsession first.")
        return

    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await message.answer("Session error: missing session ID.")
        return
    
    # Check if filename provided as argument
    if not command or not command.args:
        await message.answer("Usage: /view <filename>\n\nExample: /view main.py")
        return
    
    filename = command.args.strip()
    session_id = active_session['id']
    
    # Get file content
    content = session_files.get_file_content(session_id, filename)
    
    if content is None:
        await message.answer(f"File not found: <code>{filename}</code>\n\nUse /files to see available files.", parse_mode="HTML")
        return
    
    # Truncate content if too long for Telegram (max 4096 chars)
    if len(content) > 4000:
        content = content[:4000] + "\n\n... (truncated, file too large)"
    
    # Escape HTML special characters for Telegram
    escaped_content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    await message.answer(
        f"<b>📄 {filename}</b>\n"
        f"<b>Session:</b> <code>{session_id[:8]}</code>\n\n"
        f"<pre><code class=\"language-python\">{escaped_content}</code></pre>",
        parse_mode="HTML"
    )

@router.message(Command("edit"))
async def cmd_edit_file(message: types.Message, command: CommandObject, state: FSMContext):
    """Edit a file in current session"""
    logger.info(f"cmd_edit_file called by user {message.from_user.id}")
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if active_session is None:
        await message.answer("You need an active session. Use /newsession first.")
        return

    if 'id' not in active_session:
        logger.error(f"Active session missing 'id' key: {active_session}")
        await message.answer("Session error: missing session ID.")
        return
    
    # Check if filename provided as argument
    if not command or not command.args:
        await message.answer("Usage: /edit <filename>\n\nExample: /edit main.py")
        return
    
    filename = command.args.strip()
    session_id = active_session['id']
    
    # Check if file exists
    content = session_files.get_file_content(session_id, filename)
    
    if content is None:
        # File doesn't exist, ask if they want to create new file
        await state.update_data(edit_filename=filename, edit_session_id=session_id)
        await message.answer(
            f"File <code>{filename}</code> doesn't exist in current session.\n\n"
            f"Do you want to create it? Send the content for the new file, or /cancel to abort.",
            parse_mode="HTML"
        )
        await state.set_state(EditFileStates.waiting_for_content)
    else:
        # File exists, show current content and ask for new content
        await state.update_data(edit_filename=filename, edit_session_id=session_id, edit_original_content=content)
        
        # Show first 500 chars of existing content
        preview = content[:500] + ("..." if len(content) > 500 else "")
        escaped_preview = preview.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        await message.answer(
            f"📝 Editing <code>{filename}</code>\n\n"
            f"Current content (first 500 chars):\n"
            f"<pre><code>{escaped_preview}</code></pre>\n\n"
            f"Send the new content for this file, or /cancel to abort.",
            parse_mode="HTML"
        )
        await state.set_state(EditFileStates.waiting_for_content)

@router.message(EditFileStates.waiting_for_content)
async def process_edit_content(message: types.Message, state: FSMContext):
    """Process file content for edit/create"""
    if not message.text:
        await message.answer("Please send text content for the file.")
        return
    
    data = await state.get_data()
    filename = data.get("edit_filename")
    session_id = data.get("edit_session_id")
    original_content = data.get("edit_original_content")
    
    if not filename or not session_id:
        await message.answer("Error: Session data missing. Please start over with /edit <filename>")
        await state.clear()
        return
    
    new_content = message.text
    
    # Save file
    result = session_files.save_file_to_session(session_id, filename, new_content)
    
    if result is None:
        await message.answer("❌ Failed to save file. Please try again.")
        await state.clear()
        return
    
    # Success message
    if original_content is None:
        await message.answer(f"✅ Created new file: <code>{filename}</code>", parse_mode="HTML")
    else:
        changes = len(new_content) - len(original_content)
        change_text = f"({changes:+d} characters)"
        await message.answer(f"✅ Updated file: <code>{filename}</code> {change_text}", parse_mode="HTML")
    
    await state.clear()

@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Cancel any ongoing operation"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("No operation to cancel.")
        return
    
    await state.clear()
    await message.answer("Operation cancelled.")