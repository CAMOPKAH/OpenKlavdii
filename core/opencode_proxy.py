import asyncio
import aiohttp
from typing import Dict, Any, List, Optional
import logging
import subprocess
import json
import re
import os
from pathlib import Path
from core import session_files
from core.file_tracker import FileChangeTracker

logger = logging.getLogger("opencode_bot")

class OpenCodeProxy:
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')
        self.session: aiohttp.ClientSession | None = None
    
    async def ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False),
                timeout=aiohttp.ClientTimeout(total=60)
            )
        assert self.session is not None
    
    async def create_session(self, title: str = "Telegram Bot Session") -> str:
        await self.ensure_session()
        assert self.session is not None
        url = f"{self.api_url}/session"
        data = {"title": title}
        
        logger.debug(f"INPUT: title='{title}', url='{url}'")
        try:
            async with self.session.post(url, json=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    session_id = result["id"]
                    logger.debug(f"OUTPUT: session_id='{session_id}'")
                    return session_id
                else:
                    error_text = await resp.text()
                    logger.error(f"FAILED: status={resp.status}, response='{error_text[:200]}'")
                    return ""
        except Exception as e:
            logger.error(f"ERROR: exception={type(e).__name__}, message='{str(e)[:200]}'")
            return ""
    
    async def get_providers(self) -> Dict[str, Any]:
        await self.ensure_session()
        assert self.session is not None
        url = f"{self.api_url}/provider"
        logger.debug(f"INPUT: url='{url}'")
        
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    try:
                        result = await resp.json()
                    except Exception as e:
                         logger.error(f"PARSE_ERROR: exception={type(e).__name__}, message='{str(e)[:200]}'")
                         text = await resp.text()
                         logger.error(f"PARSE_ERROR_RESPONSE: text='{text[:500]}'")
                         return {"all": [], "connected": []}
                    
                    if not isinstance(result, dict):
                        logger.error(f"Unexpected response type from providers API: {type(result)}")
                        return {"all": [], "connected": []}
                    
                    logger.info(f"Got providers: all={len(result.get('all', []))}, connected={len(result.get('connected', []))}")
                    connected_ids = result.get("connected", [])
                    logger.info(f"Connected provider IDs: {connected_ids}")
                    
                    all_providers = result.get("all", [])
                    for p in all_providers[:5]:
                        logger.info(f"Provider: id={p.get('id')}, name={p.get('name')}, models={list(p.get('models', {}).keys())[:3]}")
                    
                    # Log deepseek provider details if present
                    for p in all_providers:
                        if p.get('id') == 'deepseek':
                            models = p.get('models', {})
                            logger.info(f"Deepseek provider models: {list(models.keys())}")
                            break
                    
                    logger.debug(f"OUTPUT: providers_count={len(all_providers)}, connected_count={len(connected_ids)}")
                    return result
                else:
                    error_text = await resp.text()
                    logger.error(f"Failed to get providers: {resp.status}, {error_text}")
                    return {"all": [], "connected": []}
        except Exception as e:
            logger.error(f"Error getting providers: {e}")
            return {"all": [], "connected": []}
    
    async def get_default_provider(self) -> Dict[str, str]:
        providers_data = await self.get_providers()
        connected = providers_data.get("connected", [])
        all_providers = providers_data.get("all", [])
        
        if not connected:
            return {"provider_id": "", "model_id": ""}
        
        first_connected_id = connected[0]
        
        for p in all_providers:
            if p.get("id") == first_connected_id:
                models = p.get("models", {})
                first_model = list(models.keys())[0] if models else ""
                return {"provider_id": first_connected_id, "model_id": first_model}
        
        return {"provider_id": first_connected_id, "model_id": ""}
    
    async def _send_message_via_cli(self, prompt: str, provider_id: str = "", model_id: str = "", session_id: str = "", thinking_callback=None, telegram_session_id: Optional[str] = None) -> Dict[str, Any]:
        """Send message using OpenCode CLI (fallback when HTTP API doesn't work)
        
        Args:
            telegram_session_id: ID of Telegram session for folder and logging
        Returns:
            Dict with keys: 'response' (str), 'thinking' (list of str), 'events' (list)
        """
        logger.debug(f"INPUT: prompt_length={len(prompt)}, provider='{provider_id}', model='{model_id}', session='{session_id}', telegram_session='{telegram_session_id}', thinking_callback={thinking_callback is not None}")
        logger.info(f"CLI_REQUEST: provider={provider_id}, model={model_id}, prompt='{prompt[:50]}...'")
        
        # Build command
        cmd = ["opencode", "run", "--format", "json"]
        
        # Add thinking flag if supported (check if thinking blocks should be shown)
        cmd.append("--thinking")  # Enable thinking/reasoning blocks output
        
        if provider_id and model_id:
            model_spec = f"{provider_id}/{model_id}"
            cmd.extend(["-m", model_spec])
        elif model_id:
            cmd.extend(["-m", model_id])
        
        # Add session ID if provided (optional)
        if session_id:
            cmd.extend(["-s", session_id])
        
        cmd.append(prompt)
        
        # Change to session directory if telegram_session_id provided
        original_cwd = None
        session_folder = None
        if telegram_session_id:
            session_folder = session_files.get_session_folder(telegram_session_id)
            original_cwd = os.getcwd()
            os.chdir(session_folder)
            logger.info(f"Changed working directory to session folder: {session_folder}")
        
        try:
            # Run command with timeout
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            logger.info(f"Running OpenCode CLI command with 300s timeout: {' '.join(cmd[:10])}...")
            
            # Collect all output
            stdout_lines = []
            stderr_lines = []
            events = []
            text_responses = []
            thinking_blocks = []
            
            # Read stdout line by line for real-time processing
            async def read_stdout():
                nonlocal thinking_blocks, text_responses, events, stdout_lines
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    line = line.decode('utf-8', errors='ignore').rstrip()
                    stdout_lines.append(line)
                    
                    if line.strip():
                        try:
                            event = json.loads(line.strip())
                            events.append(event)
                            
                            event_type = event.get("type", "")
                            part = event.get("part", {})
                            
                            # Collect thinking/reasoning blocks in real-time
                            if event_type in ["thinking", "reasoning", "step_start", "step_finish"]:
                                thinking_text = ""
                                if event_type == "thinking" and "text" in event:
                                    thinking_text = event.get("text", "")
                                elif "part" in event and "text" in event["part"]:
                                    thinking_text = event["part"].get("text", "")
                                
                                if thinking_text:
                                    logger.info(f"Found {event_type} block: {thinking_text[:100]}...")
                                    thinking_blocks.append(thinking_text)
                                    # Call callback if provided
                                    if thinking_callback:
                                        logger.debug(f"Found thinking block, calling callback: {thinking_text[:100]}...")
                                        try:
                                            await thinking_callback(thinking_text)
                                        except Exception as e:
                                            logger.warning(f"Error in thinking callback: {e}")
                                    else:
                                        logger.debug(f"Found thinking block but no callback provided: {thinking_text[:100]}...")
                                else:
                                    logger.debug(f"Empty thinking text for event type: {event_type}")
                            
                            # Collect text responses
                            if event_type == "text":
                                text = part.get("text", "")
                                if text:
                                    text_responses.append(text)
                                    logger.debug(f"Found text response: {text[:100]}...")
                        
                        except json.JSONDecodeError:
                            logger.debug(f"Non-JSON line: {line[:100]}...")
            
            # Read stderr in background
            async def read_stderr():
                while True:
                    line = await process.stderr.readline()
                    if not line:
                        break
                    line = line.decode('utf-8', errors='ignore').rstrip()
                    stderr_lines.append(line)
                    logger.debug(f"Stderr: {line}")
            
            # Run both readers concurrently
            await asyncio.gather(
                read_stdout(),
                read_stderr()
            )
            
            # Wait for process completion
            returncode = await asyncio.wait_for(process.wait(), timeout=300)
            
            stdout_text = '\n'.join(stdout_lines)
            stderr_text = '\n'.join(stderr_lines)
            
            if returncode != 0:
                logger.error(f"CLI command failed with code {returncode}: {' '.join(cmd)}")
                logger.error(f"Stderr: {stderr_text}")
                return {
                    "response": f"❌ OpenCode CLI command failed.\n\nError: {stderr_text[:200]}\n\nPlease ensure OpenCode is installed and accessible via 'opencode' command.",
                    "thinking": [],
                    "events": [],
                    "error": True
                }
            
            logger.info(f"Parsing OpenCode CLI output, stdout lines: {len(stdout_lines)}, stderr lines: {len(stderr_lines)}")
            
            # Log thinking blocks collected
            if thinking_blocks:
                logger.info(f"Collected {len(thinking_blocks)} thinking blocks")
                for i, block in enumerate(thinking_blocks):
                    logger.debug(f"Thinking block {i+1}: {block[:100]}...")
            
            # Process text responses
            response_text = ""
            if text_responses:
                full_response = "\n".join(text_responses)
                # Try to extract code from markdown code blocks
                code_blocks = re.findall(r'```(?:python|javascript|java|cpp|c|go|rust|html|css|json)?\n(.*?)```', 
                                        full_response, re.DOTALL)
                if code_blocks:
                    response_text = code_blocks[0].strip()
                else:
                    response_text = full_response
            
            # If no text response, check if files were mentioned in output
            if not response_text:
                file_pattern = r"Файл [`\"'](.*?\.py)[`\"'] создан с кодом"
                match = re.search(file_pattern, stderr_text)
                if match:
                    filename = match.group(1)
                    try:
                        with open(filename, 'r') as f:
                            response_text = f.read()
                    except Exception as e:
                        logger.warning(f"Could not read file {filename}: {e}")
            
            # Move created files to session folder (if not already there)
            moved_files = []
            if telegram_session_id:
                file_pattern = r"Файл [`\"'](.*?\.py)[`\"'] создан с кодом"
                matches = re.findall(file_pattern, stderr_text)
                for filename in matches:
                    filepath = Path(filename)
                    # If file is already in session folder, skip moving
                    if session_folder and filepath.exists() and filepath.is_relative_to(session_folder):
                        logger.debug(f"File {filename} already in session folder, skipping move")
                        moved_files.append(str(filepath))
                    else:
                        moved = session_files.move_file_to_session(telegram_session_id, filename)
                        if moved:
                            moved_files.append(str(moved))
            
            # Log to proc.md if telegram session ID provided
            if telegram_session_id:
                try:
                    session_files.log_to_proc_md(
                        telegram_session_id,
                        prompt,
                        response_text,
                        thinking_blocks
                    )
                    logger.info(f"Logged request/response to proc.md for session {telegram_session_id}")
                except Exception as e:
                    logger.error(f"Failed to log to proc.md: {e}")
            
            return {
                "response": response_text if response_text else "No response received from OpenCode CLI",
                "thinking": thinking_blocks,
                "events": events,
                "moved_files": moved_files,
                "raw_stdout": stdout_text[:1000],  # First 1000 chars
                "raw_stderr": stderr_text[:1000]   # First 1000 chars
            }
            
        except asyncio.TimeoutError:
            logger.error(f"CLI command timed out: {' '.join(cmd)}")
            return {
                "response": "❌ OpenCode request timed out after 120 seconds.\n\nThe request took too long to complete. This could be due to:\n1. Complex code generation task\n2. Network issues\n3. OpenCode server busy\n\nTry a simpler request or try again later.",
                "thinking": [],
                "events": [],
                "error": True
            }
        except Exception as e:
            logger.error(f"Error running CLI command: {e}")
            return {
                "response": f"❌ Unexpected error running OpenCode command.\n\nError: {str(e)[:200]}\n\nPlease check OpenCode installation and try again.",
                "thinking": [],
                "events": [],
                "error": True
            }
        finally:
            # Restore original working directory if changed
            if original_cwd and os.path.exists(original_cwd):
                os.chdir(original_cwd)
                logger.debug(f"Restored working directory to: {original_cwd}")
    
    async def send_message(self, session_id: str, prompt: str, provider_id: str = "", model_id: str = "", thinking_callback=None, telegram_session_id: Optional[str] = None) -> Dict[str, Any]:
        """Send message using OpenCode CLI (HTTP API doesn't return responses)
        
        Args:
            session_id: OpenCode session ID
            telegram_session_id: Telegram session ID for folder and logging
        Returns:
            Dict with keys: 'response' (str), 'thinking' (list of str), 'events' (list)
        """
        logger.info(f"Sending message via CLI: session={session_id}, provider={provider_id}, model={model_id}, prompt_length={len(prompt)}, telegram_session={telegram_session_id}")
        logger.debug(f"Thinking callback provided: {thinking_callback is not None}")
        
        if not provider_id or not model_id:
            default = await self.get_default_provider()
            if not provider_id:
                provider_id = default["provider_id"]
            if not model_id:
                model_id = default["model_id"]
        
        # Use CLI implementation
        return await self._send_message_via_cli(prompt, provider_id, model_id, session_id, thinking_callback, telegram_session_id)
    
    async def generate_code(self, prompt: str, language: str, session_id: str, provider_id: str = "", model_id: str = "", thinking_callback=None) -> Dict[str, Any]:
        logger.info(f"generate_code called: prompt={prompt[:50]}..., language={language}, session_id={session_id}, provider={provider_id}, model={model_id}")
        
        # Get session folder path for file tracking
        session_folder_path = session_files.get_session_folder(session_id)
        file_tracker = None
        try:
            file_tracker = FileChangeTracker(Path(session_folder_path))
            await file_tracker.take_before_snapshot()
            logger.debug(f"File tracking started for session: {session_id}")
        except Exception as e:
            logger.warning(f"Failed to initialize file tracker: {e}")
        
        # Use session_id as OpenCode session ID or create new
        opencode_session_id = await self.create_session(f"Code gen: {prompt[:50]}")
        if not opencode_session_id:
            logger.error("Failed to create OpenCode session")
            return {
                "response": "Failed to create OpenCode session",
                "files": {"created": [], "modified": [], "all": []},
                "thinking": [],
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "error": True
            }
        
        full_prompt = prompt # f"Напиши код на {language} для: {prompt}. Верни только код без объяснений."
        logger.info(f"Sending prompt to OpenCode session {opencode_session_id} with provider {provider_id}, model {model_id}")
        result = await self.send_message(opencode_session_id, full_prompt, provider_id, model_id, thinking_callback, telegram_session_id=session_id)
        
        # Get file changes after generation
        file_changes = {"created": [], "modified": [], "all": []}
        if file_tracker:
            try:
                file_changes = await file_tracker.take_after_snapshot()
                logger.info(f"File changes detected: {len(file_changes['all'])} files")
            except Exception as e:
                logger.error(f"Failed to get file changes: {e}")
        
        if isinstance(result, dict):
            response = result.get("response", "")
            thinking = result.get("thinking", [])
            moved_files = result.get("moved_files", [])
            
            # Log thinking blocks
            if thinking:
                logger.info(f"Generated {len(thinking)} thinking blocks during code generation")
                for i, block in enumerate(thinking):
                    logger.debug(f"Thinking {i+1}: {block[:200]}...")
            
            if not response:
                logger.warning(f"Empty response received from OpenCode")
                response = "⚠️ Получен пустой ответ от OpenCode.\n\nВозможные причины:\n1. OpenCode использует real-time stream для доставки ответов\n2. Запрос был принят, но ответ ещё обрабатывается\n\nПопробуйте использовать OpenCode напрямую через терминал: `opencode`"
            
            logger.info(f"Received response length: {len(response)}")
            
            # Combine file changes with moved files from OpenCode
            # Ensure unique file paths
            all_detected_files = set(file_changes["all"] + moved_files)
            created_detected = set(file_changes["created"])
            modified_detected = set(file_changes["modified"])
            
            # For files detected by OpenCode but not by tracker, mark as created
            for moved_file in moved_files:
                if moved_file not in all_detected_files:
                    created_detected.add(moved_file)
            
            return {
                "response": response,
                "files": {
                    "created": list(created_detected),
                    "modified": list(modified_detected),
                    "all": list(all_detected_files)
                },
                "thinking": thinking,
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "moved_files": moved_files,
                "raw_result": result  # Keep original result for debugging
            }
        else:
            # Backward compatibility (should not happen)
            logger.warning(f"Unexpected result type: {type(result)}")
            response_text = str(result) if result else "No response received"
            return {
                "response": response_text,
                "files": file_changes,
                "thinking": [],
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "error": True
            }
    
    async def debug_code(self, code: str, error: str, session_id: str, provider_id: str = "", model_id: str = "", thinking_callback=None) -> Dict[str, Any]:
        logger.info(f"debug_code called: error={error[:50]}..., session_id={session_id}, provider={provider_id}, model={model_id}")
        
        # Get session folder path for file tracking
        session_folder_path = session_files.get_session_folder(session_id)
        file_tracker = None
        try:
            file_tracker = FileChangeTracker(Path(session_folder_path))
            await file_tracker.take_before_snapshot()
            logger.debug(f"File tracking started for debugging session: {session_id}")
        except Exception as e:
            logger.warning(f"Failed to initialize file tracker: {e}")
        
        opencode_session_id = await self.create_session(f"Debug: {error[:50]}")
        if not opencode_session_id:
            logger.error("Failed to create OpenCode session for debugging")
            return {
                "response": "Failed to create OpenCode session",
                "files": {"created": [], "modified": [], "all": []},
                "thinking": [],
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "error": True
            }
        
        prompt = f"Отладка кода на Python. Ошибка: {error}\n\nКод:\n```python\n{code}\n```\nИсправь ошибку и верни исправленный код."
        result = await self.send_message(opencode_session_id, prompt, provider_id, model_id, thinking_callback, telegram_session_id=session_id)
        
        # Get file changes after debugging
        file_changes = {"created": [], "modified": [], "all": []}
        if file_tracker:
            try:
                file_changes = await file_tracker.take_after_snapshot()
                logger.info(f"File changes detected during debugging: {len(file_changes['all'])} files")
            except Exception as e:
                logger.error(f"Failed to get file changes: {e}")
        
        if isinstance(result, dict):
            response = result.get("response", "")
            thinking = result.get("thinking", [])
            moved_files = result.get("moved_files", [])
            
            if thinking:
                logger.info(f"Generated {len(thinking)} thinking blocks during debugging")
                for i, block in enumerate(thinking):
                    logger.debug(f"Thinking {i+1}: {block[:200]}...")
            
            if not response:
                logger.warning(f"Empty response received from OpenCode during debugging")
                response = "⚠️ Получен пустой ответ от OpenCode при отладке.\n\nПопробуйте использовать OpenCode напрямую через терминал: `opencode`"
            
            logger.info(f"Received debug response length: {len(response)}")
            
            # Combine file changes with moved files from OpenCode
            all_detected_files = set(file_changes["all"] + moved_files)
            created_detected = set(file_changes["created"])
            modified_detected = set(file_changes["modified"])
            
            # For files detected by OpenCode but not by tracker, mark as created
            for moved_file in moved_files:
                if moved_file not in all_detected_files:
                    created_detected.add(moved_file)
            
            return {
                "response": response,
                "files": {
                    "created": list(created_detected),
                    "modified": list(modified_detected),
                    "all": list(all_detected_files)
                },
                "thinking": thinking,
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "moved_files": moved_files,
                "raw_result": result
            }
        else:
            # Backward compatibility (should not happen)
            logger.warning(f"Unexpected result type from debugging: {type(result)}")
            response_text = str(result) if result else "No response received"
            return {
                "response": response_text,
                "files": file_changes,
                "thinking": [],
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "error": True
            }
    
    async def refactor_code(self, code: str, focus: str, session_id: str, provider_id: str = "", model_id: str = "", thinking_callback=None) -> Dict[str, Any]:
        logger.info(f"refactor_code called: focus={focus[:50]}..., session_id={session_id}, provider={provider_id}, model={model_id}")
        
        # Get session folder path for file tracking
        session_folder_path = session_files.get_session_folder(session_id)
        file_tracker = None
        try:
            file_tracker = FileChangeTracker(Path(session_folder_path))
            await file_tracker.take_before_snapshot()
            logger.debug(f"File tracking started for refactoring session: {session_id}")
        except Exception as e:
            logger.warning(f"Failed to initialize file tracker: {e}")
        
        opencode_session_id = await self.create_session(f"Refactor: {focus[:50]}")
        if not opencode_session_id:
            logger.error("Failed to create OpenCode session for refactoring")
            return {
                "response": "Failed to create OpenCode session",
                "files": {"created": [], "modified": [], "all": []},
                "thinking": [],
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "error": True
            }
        
        prompt = f"Рефакторинг кода на Python. Фокус: {focus}\n\nКод:\n```python\n{code}\n```\nОптимизируй код и верни улучшенную версию."
        result = await self.send_message(opencode_session_id, prompt, provider_id, model_id, thinking_callback, telegram_session_id=session_id)
        
        # Get file changes after refactoring
        file_changes = {"created": [], "modified": [], "all": []}
        if file_tracker:
            try:
                file_changes = await file_tracker.take_after_snapshot()
                logger.info(f"File changes detected during refactoring: {len(file_changes['all'])} files")
            except Exception as e:
                logger.error(f"Failed to get file changes: {e}")
        
        if isinstance(result, dict):
            response = result.get("response", "")
            thinking = result.get("thinking", [])
            moved_files = result.get("moved_files", [])
            
            if thinking:
                logger.info(f"Generated {len(thinking)} thinking blocks during refactoring")
                for i, block in enumerate(thinking):
                    logger.debug(f"Thinking {i+1}: {block[:200]}...")
            
            if not response:
                logger.warning(f"Empty response received from OpenCode during refactoring")
                response = "⚠️ Получен пустой ответ от OpenCode при рефакторинге.\n\nПопробуйте использовать OpenCode напрямую через терминал: `opencode`"
            
            logger.info(f"Received refactoring response length: {len(response)}")
            
            # Combine file changes with moved files from OpenCode
            all_detected_files = set(file_changes["all"] + moved_files)
            created_detected = set(file_changes["created"])
            modified_detected = set(file_changes["modified"])
            
            # For files detected by OpenCode but not by tracker, mark as created
            for moved_file in moved_files:
                if moved_file not in all_detected_files:
                    created_detected.add(moved_file)
            
            return {
                "response": response,
                "files": {
                    "created": list(created_detected),
                    "modified": list(modified_detected),
                    "all": list(all_detected_files)
                },
                "thinking": thinking,
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "moved_files": moved_files,
                "raw_result": result
            }
        else:
            # Backward compatibility (should not happen)
            logger.warning(f"Unexpected result type from refactoring: {type(result)}")
            response_text = str(result) if result else "No response received"
            return {
                "response": response_text,
                "files": file_changes,
                "thinking": [],
                "session_folder": str(session_folder_path),
                "telegram_session_id": session_id,
                "error": True
            }
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

# Global instance
from core.config import settings
opencode_client = OpenCodeProxy(settings.opencode_api_url)
