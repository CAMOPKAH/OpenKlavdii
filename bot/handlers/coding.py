from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from core.session_manager import session_manager
from core.opencode_proxy import opencode_client

router = Router()

class GenerateStates(StatesGroup):
    waiting_for_prompt = State()

@router.message(Command("generate"))
async def cmd_generate(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    
    if not active_session:
        await message.answer("You need an active session. Use /new_session")
        return

    await message.answer("Describe the coding task you want me to solve:")
    await state.set_state(GenerateStates.waiting_for_prompt)

@router.message(GenerateStates.waiting_for_prompt)
async def process_generation_prompt(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    active_session = await session_manager.get_active_session(user_id)
    session_id = active_session['id']
    prompt = message.text

    await message.answer("Generating code... Please wait.")
    
    # Call OpenCode Proxy
    result = await opencode_client.generate_code(prompt, "python", session_id)
    
    await message.answer(f"Generated Code:\n\n```python{result}```", parse_mode="Markdown")
    await state.clear()

@router.message(Command("debug"))
async def cmd_debug(message: types.Message):
    await message.answer("Please reply to a code block or send a file with the caption /debug to analyze it.")

@router.message(Command("refactor"))
async def cmd_refactor(message: types.Message):
    await message.answer("Please reply to a code block with /refactor to optimize it.")
