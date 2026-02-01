import uuid
from typing import Dict, Optional, Any
import datetime

class SessionManager:
    def __init__(self):
        # In-memory storage: {user_id: {session_id: data}}
        # Active session pointer: {user_id: active_session_id}
        self.sessions: Dict[int, Dict[str, Any]] = {} 
        self.active_sessions: Dict[int, str] = {}

    async def create_session(self, user_id: int) -> str:
        session_id = str(uuid.uuid4())
        timestamp = datetime.datetime.now().isoformat()
        
        if user_id not in self.sessions:
            self.sessions[user_id] = {}
            
        self.sessions[user_id][session_id] = {
            "created_at": timestamp,
            "messages": [],
            "context": {},
            "id": session_id
        }
        self.active_sessions[user_id] = session_id
        return session_id

    async def get_active_session(self, user_id: int) -> Optional[dict]:
        session_id = self.active_sessions.get(user_id)
        if not session_id:
            return None
        return self.sessions.get(user_id, {}).get(session_id)

    async def list_user_sessions(self, user_id: int) -> list:
        if user_id not in self.sessions:
            return []
        return list(self.sessions[user_id].values())

    async def switch_session(self, user_id: int, session_id: str) -> bool:
        if user_id in self.sessions and session_id in self.sessions[user_id]:
            self.active_sessions[user_id] = session_id
            return True
        return False

# Global instance
session_manager = SessionManager()
