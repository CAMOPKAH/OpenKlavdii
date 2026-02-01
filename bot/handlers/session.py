from aiogram import Router, types
from aiogram.filters import Command
from core.session_manager import session_manager

router = Router()

@router.message(Command("new_session"))
async def cmd_new_session(message: types.Message):
    user_id = message.from_user.id
    session_id = await session_manager.create_session(user_id)
    await message.answer(f"✅ Created new session!\n<b>Session ID:</b> <code>{session_id}</code>\n\nRefer to this session for coding tasks.", parse_mode="HTML")

@router.message(Command("list_sessions"))
async def cmd_list_sessions(message: types.Message):
    user_id = message.from_user.id
    sessions = await session_manager.list_user_sessions(user_id)
    
    if not sessions:
        await message.answer("No active sessions found. Start one with /new_session")
        return

    text = "<b>📋 Your Sessions:</b>\n"
    active_session_data = await session_manager.get_active_session(user_id)
    active_id = active_session_data['id'] if active_session_data else None

    for s in sessions:
        status = "🟢 (Active)" if s['id'] == active_id else ""
        text += f"- <code>{s['id']}</code> {status}\n  Created: {s['created_at']}\n"
    
    await message.answer(text, parse_mode="HTML")

@router.message(Command("switch_session"))
async def cmd_switch_session(message: types.Message):
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Usage: /switch_session <session_id>")
        return
    
    session_id = args[1]
    user_id = message.from_user.id
    success = await session_manager.switch_session(user_id, session_id)
    
    if success:
        await message.answer(f"🔄 Switched to session: <code>{session_id}</code>", parse_mode="HTML")
    else:
        await message.answer("❌ Session not found.", parse_mode="HTML")
