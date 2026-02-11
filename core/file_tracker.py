"""
File change tracking utility for OpenKlavdii bot.
Tracks created and modified files in session folders.
"""
import hashlib
import asyncio
from pathlib import Path
from typing import Dict, List, Set, Optional
import logging

logger = logging.getLogger("opencode_bot")


class FileChangeTracker:
    """Track file changes in session folders."""
    
    def __init__(self, session_folder: Path):
        self.session_folder = session_folder
        self.before_snapshot: Dict[Path, str] = {}  # filepath → md5 hash
        self._exclude_patterns: Set[str] = {
            "__pycache__", ".git", ".env", ".DS_Store", "Thumbs.db",
            "*.pyc", "*.pyo", "*.swp", ".vscode", ".idea", "node_modules",
            ".gitignore", ".gitmodules", ".hg", ".svn", ".bzr"
        }
    
    def _should_exclude(self, filepath: Path) -> bool:
        """Determine if file should be excluded from tracking."""
        # Convert to string for pattern matching
        path_str = str(filepath)
        
        # Check absolute path patterns
        for pattern in self._exclude_patterns:
            if pattern in path_str:
                return True
        
        # Check file extension patterns
        if filepath.suffix in {'.pyc', '.pyo', '.swp'}:
            return True
        
        # Check hidden files (Unix) starting with .
        if filepath.name.startswith('.'):
            return True
        
        return False
    
    async def _get_file_hash(self, filepath: Path) -> Optional[str]:
        """Calculate MD5 hash of a file asynchronously."""
        try:
            # Use thread pool for blocking I/O
            def _compute_hash():
                with open(filepath, 'rb') as f:
                    return hashlib.md5(f.read()).hexdigest()
            
            return await asyncio.to_thread(_compute_hash)
        except Exception as e:
            logger.debug(f"Failed to hash file {filepath}: {e}")
            return None
    
    async def _get_file_hashes(self) -> Dict[Path, str]:
        """Get MD5 hashes of all files in session folder."""
        hashes = {}
        
        # Recursively walk through session folder
        for filepath in self.session_folder.rglob("*"):
            if filepath.is_file() and not self._should_exclude(filepath):
                file_hash = await self._get_file_hash(filepath)
                if file_hash:
                    hashes[filepath] = file_hash
        
        logger.debug(f"Collected hashes for {len(hashes)} files in {self.session_folder}")
        return hashes
    
    async def take_before_snapshot(self):
        """Take snapshot of file state before code generation."""
        logger.info(f"Taking before snapshot for session: {self.session_folder}")
        self.before_snapshot = await self._get_file_hashes()
        logger.debug(f"Before snapshot: {len(self.before_snapshot)} files")
    
    async def take_after_snapshot(self) -> Dict[str, List[str]]:
        """Compare file state after code generation, return changes."""
        logger.info(f"Taking after snapshot for session: {self.session_folder}")
        after_snapshot = await self._get_file_hashes()
        logger.debug(f"After snapshot: {len(after_snapshot)} files")
        
        created = []
        modified = []
        
        # Find created files
        for filepath in after_snapshot:
            if filepath not in self.before_snapshot:
                rel_path = str(filepath.relative_to(self.session_folder))
                created.append(rel_path)
        
        # Find modified files
        for filepath, after_hash in after_snapshot.items():
            if filepath in self.before_snapshot:
                before_hash = self.before_snapshot[filepath]
                if after_hash != before_hash:
                    rel_path = str(filepath.relative_to(self.session_folder))
                    modified.append(rel_path)
        
        # Clean up snapshots to free memory
        self.before_snapshot.clear()
        
        result = {
            "created": created,
            "modified": modified,
            "all": created + modified
        }
        
        logger.info(f"File changes detected: {len(created)} created, {len(modified)} modified")
        return result
    
    def get_file_size(self, rel_path: str) -> int:
        """Get file size in bytes."""
        filepath = self.session_folder / rel_path
        if filepath.exists():
            return filepath.stat().st_size
        return 0
    
    def get_file_size_readable(self, rel_path: str) -> str:
        """Get human-readable file size."""
        size_bytes = self.get_file_size(rel_path)
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"