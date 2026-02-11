from aiogram import Router, types, F
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging

from core.session_manager import session_manager
from core.opencode_proxy import opencode_client

router = Router()
logger = logging.getLogger("opencode_bot")

class ProviderStates(StatesGroup):
    choosing_provider = State()
    choosing_model = State()


async def build_providers_keyboard(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build providers list text and inline keyboard."""
    user_prefs = await session_manager.get_user_preference(user_id)
    current_provider = user_prefs.get("provider_id", "OpenCode (auto)")
    current_model = user_prefs.get("model_id", "")
    
    # Get providers from OpenCode
    providers_data = await opencode_client.get_providers()
    all_providers = providers_data.get("all", [])
    connected = providers_data.get("connected", [])
    
    # Filter to show only connected providers
    connected_providers = [p for p in all_providers if p["id"] in connected]
    
    logger.info(f"build_providers_keyboard: user={user_id}, current_provider={current_provider}, connected_count={len(connected_providers)}")
    for p in connected_providers[:10]:  # Log first 10 connected providers
        logger.info(f"Connected provider: id={p.get('id')}, name={p.get('name')}")
    
    if not connected_providers:
        text = (
            "⚠️ No connected providers found in OpenCode.\n\n"
            "Please connect a provider in OpenCode first:\n"
            "1. Open OpenCode TUI: `opencode`\n"
            "2. Use `/connect` command\n"
            "3. Choose a provider (e.g., OpenCode Zen, OpenAI, Anthropic)\n"
            "4. Configure API key\n"
            "5. Restart the bot"
        )
        return text, None
    
    text = f"**Current selection:** `{current_provider}` / `{current_model}`\n\n"
    text += "**Select a provider:**\n"
    
    builder = InlineKeyboardBuilder()
    
    for provider in connected_providers:
        provider_id = provider["id"]
        provider_name = provider["name"]
        is_current = "✅ " if provider_id == current_provider else ""
        builder.add(InlineKeyboardButton(
            text=f"{is_current}{provider_name}",
            callback_data=f"provider:{provider_id}"
        ))
    
    builder.adjust(1)  # One button per row
    
    return text, builder.as_markup()


async def build_models_keyboard(user_id: int, provider_id: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build models list for a provider."""
    user_prefs = await session_manager.get_user_preference(user_id)
    current_model = user_prefs.get("model_id", "")
    
    # Get provider info
    providers_data = await opencode_client.get_providers()
    all_providers = providers_data.get("all", [])
    connected = providers_data.get("connected", [])
    
    provider_info = None
    for p in all_providers:
        if p["id"] == provider_id and p["id"] in connected:
            provider_info = p
            break
    
    if not provider_info:
        return "❌ Provider not found.", None
    
    models = provider_info.get("models", {})
    if not models:
        return "❌ Provider has no models.", None
    
    text = f"**Select model for {provider_info['name']}:**\n"
    text += f"Current model: `{current_model}`\n\n"
    
    builder = InlineKeyboardBuilder()
    
    for model_id in models.keys():
        is_current = "✅ " if model_id == current_model else ""
        builder.add(InlineKeyboardButton(
            text=f"{is_current}{model_id}",
            callback_data=f"model:{provider_id}:{model_id}"
        ))
    
    builder.add(InlineKeyboardButton(
        text="⬅️ Back to providers",
        callback_data="providers:back"
    ))
    builder.adjust(1)
    
    return text, builder.as_markup()


@router.message(or_f(Command("providers"), Command("provider"), Command("model")))
async def cmd_providers(message: types.Message):
    """Show available providers and current selection with inline keyboard"""
    user_id = message.from_user.id
    text, keyboard = await build_providers_keyboard(user_id)
    
    if keyboard:
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="Markdown")

@router.callback_query(F.data.startswith("provider:"))
async def callback_provider_selection(callback: CallbackQuery):
    """Handle provider selection from inline keyboard"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    if callback.data is None:
        await callback.answer("❌ Invalid data", show_alert=True)
        return
    provider_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    # Get provider info
    providers_data = await opencode_client.get_providers()
    all_providers = providers_data.get("all", [])
    connected = providers_data.get("connected", [])
    
    logger.info(f"Provider selection: user={user_id}, requested provider={provider_id}")
    logger.info(f"Connected providers: {connected}")
    logger.info(f"All providers count: {len(all_providers)}")
    
    # Validate provider
    provider_info = None
    for p in all_providers:
        if p["id"] == provider_id:
            logger.info(f"Found provider {provider_id}: connected={p['id'] in connected}")
            if p["id"] in connected:
                provider_info = p
                break
    
    if not provider_info:
        await callback.answer("❌ Provider not available", show_alert=True)
        return
    
    # Get models for this provider
    models = provider_info.get("models", {})
    if not models:
        await callback.answer("❌ Provider has no models", show_alert=True)
        return
    
    # Set default model (first one)
    model_id = list(models.keys())[0]
    await session_manager.set_user_preference(user_id, provider_id, model_id)
    
    # Show model selection keyboard
    text, keyboard = await build_models_keyboard(user_id, provider_id)
    if keyboard:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.edit_text(text, parse_mode="Markdown")
    
    await callback.answer(f"✅ Selected {provider_info['name']}")


@router.callback_query(F.data.startswith("model:"))
async def callback_model_selection(callback: CallbackQuery):
    """Handle model selection from inline keyboard"""
    if callback.message is None:
        await callback.answer()
        return
    message = callback.message
    if callback.data is None:
        await callback.answer("❌ Invalid data", show_alert=True)
        return
    data_parts = callback.data.split(":")
    if len(data_parts) != 3:
        await callback.answer("❌ Invalid data", show_alert=True)
        return
    
    provider_id = data_parts[1]
    model_id = data_parts[2]
    user_id = callback.from_user.id
    
    # Validate provider and model
    providers_data = await opencode_client.get_providers()
    all_providers = providers_data.get("all", [])
    connected = providers_data.get("connected", [])
    
    provider_info = None
    for p in all_providers:
        if p["id"] == provider_id and p["id"] in connected:
            provider_info = p
            break
    
    if not provider_info:
        await callback.answer("❌ Provider not available", show_alert=True)
        return
    
    models = provider_info.get("models", {})
    if model_id not in models:
        await callback.answer("❌ Model not found", show_alert=True)
        return
    
    # Save preferences
    await session_manager.set_user_preference(user_id, provider_id, model_id)
    
    # Show updated model list
    text, keyboard = await build_models_keyboard(user_id, provider_id)
    if keyboard:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.edit_text(text, parse_mode="Markdown")
    
    await callback.answer(f"✅ Selected {model_id}")


@router.callback_query(F.data == "providers:back")
async def callback_back_to_providers(callback: CallbackQuery):
    """Return to providers list"""
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


@router.message(Command("setprovider"))
async def cmd_setprovider(message: types.Message):
    """Set provider for current user"""
    args = message.text.split()
    if len(args) < 2:
        # Show providers keyboard instead of usage text
        await cmd_providers(message)
        return
    
    provider_id = args[1]
    user_id = message.from_user.id
    
    # Get providers to validate
    providers_data = await opencode_client.get_providers()
    all_providers = providers_data.get("all", [])
    connected = providers_data.get("connected", [])
    
    # Check if provider exists and is connected
    provider_info = None
    for p in all_providers:
        if p["id"] == provider_id and p["id"] in connected:
            provider_info = p
            break
    
    if not provider_info:
        await message.answer(
            f"❌ Provider `{provider_id}` not found or not connected.\n"
            "Use `/providers` to see available providers."
        )
        return
    
    # Get first model from provider as default
    models = provider_info.get("models", {})
    if not models:
        await message.answer(f"❌ Provider `{provider_id}` has no models available.")
        return
    
    model_id = list(models.keys())[0]
    
    # Save preferences
    await session_manager.set_user_preference(user_id, provider_id, model_id)
    
    await message.answer(
        f"✅ Provider set to **{provider_info['name']}** (`{provider_id}`)\n"
        f"Default model: **{model_id}**\n\n"
        "Use `/setmodel` to change the model."
    )

@router.message(Command("setmodel"))
async def cmd_setmodel(message: types.Message):
    """Set specific model for current provider"""
    args = message.text.split()
    if len(args) < 3:
        # Show providers keyboard instead of usage text
        await cmd_providers(message)
        return
    
    provider_id = args[1]
    model_id = args[2]
    user_id = message.from_user.id
    
    # Get providers to validate
    providers_data = await opencode_client.get_providers()
    all_providers = providers_data.get("all", [])
    connected = providers_data.get("connected", [])
    
    # Check if provider exists and is connected
    provider_info = None
    for p in all_providers:
        if p["id"] == provider_id and p["id"] in connected:
            provider_info = p
            break
    
    if not provider_info:
        await message.answer(
            f"❌ Provider `{provider_id}` not found or not connected.\n"
            "Use `/providers` to see available providers."
        )
        return
    
    # Check if model exists
    models = provider_info.get("models", {})
    if model_id not in models:
        available_models = ", ".join(list(models.keys())[:5])
        if len(models) > 5:
            available_models += f" ... and {len(models)-5} more"
        
        await message.answer(
            f"❌ Model `{model_id}` not found in provider `{provider_id}`.\n"
            f"Available models: {available_models}"
        )
        return
    
    # Save preferences
    await session_manager.set_user_preference(user_id, provider_id, model_id)
    
    await message.answer(
        f"✅ Model set to **{model_id}** for provider **{provider_info['name']}**\n"
        f"Now using: `{provider_id}` / `{model_id}`"
    )