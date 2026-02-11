import uuid
from typing import Dict, Optional, Any
import datetime
import os
import logging
from pathlib import Path
from core.config import settings
from core.opencode_proxy import opencode_client

logger = logging.getLogger("opencode_bot")

class SessionManager:
    def __init__(self):
        # In-memory storage: {user_id: {session_id: data}}
        # Active session pointer: {user_id: active_session_id}
        self.sessions: Dict[int, Dict[str, Any]] = {} 
        self.active_sessions: Dict[int, str] = {}
        # User provider/model preferences: {user_id: {"provider_id": str, "model_id": str, "show_thinking": bool}}
        self.user_preferences: Dict[int, Dict[str, Any]] = {}

    async def create_session(self, user_id: int) -> str:
        logger.debug(f"INPUT: user_id={user_id}")
        session_id = str(uuid.uuid4())
        timestamp = datetime.datetime.now().isoformat()
        
        # Create session folder
        session_folder = Path(f"work_place/{session_id}")
        session_folder.mkdir(parents=True, exist_ok=True)
        
        if user_id not in self.sessions:
            self.sessions[user_id] = {}
            
        self.sessions[user_id][session_id] = {
            "created_at": timestamp,
            "messages": [],
            "context": {},
            "id": session_id,
            "folder": str(session_folder)
        }
        self.active_sessions[user_id] = session_id
        logger.debug(f"OUTPUT: session_id='{session_id}', folder='{session_folder}', sessions_count={len(self.sessions.get(user_id, {}))}")
        return session_id

    async def get_active_session(self, user_id: int) -> Optional[dict]:
        logger.debug(f"INPUT: user_id={user_id}")
        session_id = self.active_sessions.get(user_id)
        if not session_id:
            logger.debug(f"OUTPUT: no active session for user={user_id}")
            return None
        session = self.sessions.get(user_id, {}).get(session_id)
        logger.debug(f"OUTPUT: session_id='{session_id}', session_exists={session is not None}")
        return session

    async def list_user_sessions(self, user_id: int) -> list:
        if user_id not in self.sessions:
            return []
        return list(self.sessions[user_id].values())

    async def switch_session(self, user_id: int, session_id: str) -> bool:
        if user_id in self.sessions and session_id in self.sessions[user_id]:
            self.active_sessions[user_id] = session_id
            return True
        return False

    # Provider/Model preferences
    async def set_user_preference(self, user_id: int, provider_id: str, model_id: str) -> None:
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        self.user_preferences[user_id]["provider_id"] = provider_id
        self.user_preferences[user_id]["model_id"] = model_id

    async def get_user_preference(self, user_id: int) -> Dict[str, str]:
        if user_id not in self.user_preferences:
            default = await opencode_client.get_default_provider()
            return default
        # Return only provider_id and model_id (strings)
        prefs = self.user_preferences[user_id]
        result = {}
        if "provider_id" in prefs:
            result["provider_id"] = str(prefs["provider_id"])
        if "model_id" in prefs:
            result["model_id"] = str(prefs["model_id"])
        return result
    
    async def set_thinking_preference(self, user_id: int, enabled: bool):
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        self.user_preferences[user_id]["show_thinking"] = enabled
    
    async def get_thinking_preference(self, user_id: int) -> bool:
        import logging
        logger = logging.getLogger("opencode_bot")
        if user_id not in self.user_preferences:
            logger.debug(f"get_thinking_preference: user {user_id} not in preferences, default True")
            return True  # default enabled
        result = self.user_preferences[user_id].get("show_thinking", True)
        logger.debug(f"get_thinking_preference: user {user_id} = {result}")
        return result
    
    async def get_session_folder(self, user_id: int) -> Optional[Path]:
        """Get the folder path for the active session"""
        session = await self.get_active_session(user_id)
        if not session:
            return None
        folder = session.get("folder")
        if not folder:
            return None
        return Path(folder)

# Global instance
session_manager = SessionManager()
