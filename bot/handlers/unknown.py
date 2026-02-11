from aiogram import Router, types

router = Router()

@router.message()
async def handle_unknown(message: types.Message):
    if message.text and message.text.startswith('/'):
        await message.answer("Unknown command. Use /help to see available commands.")
    elif message.text:
        await message.answer(
            "I can help you with coding tasks! Please use one of the commands:\n"
            "/generate - Generate code\n"
            "/debug - Debug code\n"
            "/refactor - Refactor code\n\n"
            "Or use /help to see all available commands."
        )