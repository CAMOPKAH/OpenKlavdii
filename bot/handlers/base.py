import logging
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command, or_f
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.session_manager import session_manager
from bot.handlers.providers import build_providers_keyboard

router = Router()
logger = logging.getLogger("opencode_bot")

def create_main_keyboard() -> InlineKeyboardMarkup:
    """Create main menu inline keyboard"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="🤖 Выбор провайдера", callback_data="menu:providers"),
        InlineKeyboardButton(text="💻 Новая сессия", callback_data="menu:newsession"),
        InlineKeyboardButton(text="📋 Список сессий", callback_data="menu:listsessions"),
        InlineKeyboardButton(text="🧠 Задать вопрос", callback_data="menu:question"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings")
    )
    builder.adjust(2, 2, 2)
    return builder.as_markup()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    logger.info(f"cmd_start called by user {message.from_user.id}")
    keyboard = create_main_keyboard()
    await message.answer(
        "👋 Welcome to OpenCode AI Bot!\n\n"
        "I am your bridge to the OpenCode coding agent.\n"
        "Use кнопки ниже для быстрого доступа к функциям или /help для всех команд.",
        reply_markup=keyboard
    )

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🤖 **Available Commands:**\n\n"
        "📝 **Session Management**\n"
        "/newsession - Start a new coding session\n"
        "/listsessions - Show active sessions\n"
        "/switchsession <id> - Switch context\n\n"
        "💻 **Coding**\n"
        "/generate - Generate code\n"
        "/debug - Debug code\n"
        "/refactor - Refactor code\n"
        "/ask - Ask questions about code (interactive)\n\n"
        "📁 **File Management**\n"
        "/files - List files in current session\n"
        "/view <filename> - View file content\n"
        "/edit <filename> - Edit or create file\n"
        "/publish - Publish session to GitHub\n\n"
        "🤖 **AI Models**\n"
        "/providers - Show available AI providers\n"
        "/setprovider <id> - Set provider (use ID from /providers)\n"
        "/setmodel <provider> <model> - Set specific model\n\n"
        "⚙️ **Tools**\n"
        "/settings - Toggle thinking display and publish\n"
        "/cancel - Cancel current operation\n"
        "/githubconnect - Connect GitHub account (coming soon)"
    )
    await message.answer(help_text, parse_mode="Markdown", reply_markup=create_main_keyboard())

@router.message(or_f(Command("github_connect"), Command("githubconnect"), Command("gh")))
async def cmd_github_connect(message: types.Message):
    await message.answer("GitHub integration coming soon!")

@router.callback_query(F.data == "menu:providers")
async def callback_menu_providers(callback: CallbackQuery):
    """Handle providers menu button"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    user_id = callback.from_user.id
    text, keyboard = await build_providers_keyboard(user_id)
    if keyboard:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "menu:newsession")
async def callback_menu_newsession(callback: CallbackQuery):
    """Handle new session menu button"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    user_id = callback.from_user.id
    session_id = await session_manager.create_session(user_id)
    await message.edit_text(
        f"✅ Новая сессия создана!\nID: `{session_id}`\n\n"
        "Теперь вы можете использовать команды /generate, /debug, /refactor.",
        parse_mode="Markdown",
        reply_markup=create_main_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "menu:listsessions")
async def callback_menu_listsessions(callback: CallbackQuery):
    """Handle list sessions menu button"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    user_id = callback.from_user.id
    sessions = await session_manager.list_user_sessions(user_id)
    
    if not sessions:
        text = "У вас нет активных сессий. Используйте «Новая сессия» чтобы начать."
        await message.edit_text(text, reply_markup=create_main_keyboard())
        await callback.answer()
        return
    
    text = "📋 Ваши активные сессии:\n\n"
    active_session = await session_manager.get_active_session(user_id)
    active_session_id = active_session['id'] if active_session else None
    
    for session in sessions:
        session_id = session['id']
        created = session['created_at'][:19]  # Trim microseconds
        is_active = " ✅" if session_id == active_session_id else ""
        text += f"• `{session_id}`\n  Создана: {created}{is_active}\n\n"
    
    text += "Используйте /switchsession <id> для переключения."
    await message.edit_text(text, parse_mode="Markdown", reply_markup=create_main_keyboard())
    await callback.answer()

@router.callback_query(F.data == "menu:help")
async def callback_menu_help(callback: CallbackQuery):
    """Handle help menu button"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    help_text = (
        "🤖 **Available Commands:**\n\n"
        "📝 **Session Management**\n"
        "/newsession - Start a new coding session\n"
        "/listsessions - Show active sessions\n"
        "/switchsession <id> - Switch context\n\n"
        "💻 **Coding**\n"
        "/generate - Generate code\n"
        "/debug - Debug code\n"
        "/refactor - Refactor code\n"
        "/ask - Ask questions about code (interactive)\n\n"
        "📁 **File Management**\n"
        "/files - List files in current session\n"
        "/view <filename> - View file content\n"
        "/edit <filename> - Edit or create file\n"
        "/publish - Publish session to GitHub\n\n"
        "🤖 **AI Models**\n"
        "/providers - Show available AI providers\n"
        "/setprovider <id> - Set provider (use ID from /providers)\n"
        "/setmodel <provider> <model> - Set specific model\n\n"
        "⚙️ **Tools**\n"
        "/settings - Toggle thinking display and publish\n"
        "/cancel - Cancel current operation\n"
        "/githubconnect - Connect GitHub account (coming soon)"
    )
    await message.edit_text(help_text, parse_mode="Markdown", reply_markup=create_main_keyboard())
    await callback.answer()

@router.callback_query(F.data == "menu:settings")
async def callback_menu_settings(callback: CallbackQuery):
    """Handle settings menu button"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    user_id = callback.from_user.id
    user_prefs = await session_manager.get_user_preference(user_id)
    current_provider = user_prefs.get("provider_id", "OpenCode (auto)")
    current_model = user_prefs.get("model_id", "")
    
    text = (
        f"⚙️ **Текущие настройки:**\n\n"
        f"**Провайдер:** `{current_provider}`\n"
        f"**Модель:** `{current_model}`\n\n"
        "Используйте «Выбор провайдера» для изменения настроек."
    )
    await message.edit_text(text, parse_mode="Markdown", reply_markup=create_main_keyboard())
    await callback.answer()

@router.callback_query(F.data == "menu:question")
async def callback_menu_question(callback: CallbackQuery):
    """Handle question menu button"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    user_id = callback.from_user.id
    
    text = (
        "🧠 **Задать вопрос по коду**\n\n"
        "Я могу помочь вам с:\n"
        "• 📝 Объяснением кода\n"
        "• 🚀 Улучшением кода\n"
        "• 🔤 Переводом между языками\n"
        "• 🧮 Объяснением алгоритмов\n"
        "• 🐛 Поиском ошибок\n"
        "• 🧪 Написанием тестов\n\n"
        "Используйте команду /ask чтобы начать, или отправьте код с вопросом."
    )
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="📝 Начать вопрос", callback_data="question:start"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:back")
    )
    builder.adjust(1)
    
    await message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data == "menu:back")
async def callback_menu_back(callback: CallbackQuery):
    """Return to main menu"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    await message.edit_text(
        "👋 Welcome to OpenCode AI Bot!\n\n"
        "I am your bridge to the OpenCode coding agent.\n"
        "Use кнопки ниже для быстрого доступа к функциим или /help для всех команд.",
        reply_markup=create_main_keyboard()
    )
    await callback.answer()
