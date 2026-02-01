import asyncio
import sys
import os
import unittest

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.session_manager import SessionManager
from core.opencode_proxy import OpenCodeProxy

class TestCoreComponents(unittest.TestCase):
    def setUp(self):
        self.session_manager = SessionManager()
        self.proxy = OpenCodeProxy("http://mock-url")

    def test_session_creation(self):
        async def run():
            user_id = 12345
            session_id = await self.session_manager.create_session(user_id)
            self.assertIsNotNone(session_id)
            
            sessions = await self.session_manager.list_user_sessions(user_id)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]['id'], session_id)
            
            active = await self.session_manager.get_active_session(user_id)
            self.assertEqual(active['id'], session_id)
        
        asyncio.run(run())

    def test_proxy_generation(self):
        async def run():
            result = await self.proxy.generate_code("print hello", "python", "sess-1")
            self.assertIn("def solve_problem():", result)
            self.assertIn("sess-1", result)
        
        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
