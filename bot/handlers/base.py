from aiogram import Router, types
from aiogram.filters import CommandStart, Command

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Welcome to OpenCode AI Bot!\n\n"
        "I am your bridge to the OpenCode coding agent.\n"
        "Use /new_session to start a coding session or /help to see all commands."
    )

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🤖 **Available Commands:**\n\n"
        "📝 **Session Management**\n"
        "/new_session - Start a new coding session\n"
        "/list_sessions - Show active sessions\n"
        "/switch_session <id> - Switch context\n\n"
        "💻 **Coding**\n"
        "/generate - Generate code\n"
        "/debug - Debug code\n"
        "/refactor - Refactor code\n\n"
        "⚙️ **Tools**\n"
        "/model - Select LLM model\n"
        "/github_connect - Connect GitHub account"
    )
    await message.answer(help_text, parse_mode="Markdown")
