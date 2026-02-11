from pydantic_settings import BaseSettings
from pydantic import SecretStr
from typing import List, Optional

class Settings(BaseSettings):
    bot_token: SecretStr
    redis_url: str = "redis://localhost:6379/0"
    opencode_api_url: str = "http://localhost:8000"
    
    # File handling settings
    max_files_before_archive: int = 10
    max_file_size_mb: int = 45  # Telegram limit is 50MB, leave margin
    max_archive_size_mb: int = 45
    
    # File exclusion patterns
    excluded_file_patterns: List[str] = [
        "__pycache__", ".git", ".env", ".DS_Store", "Thumbs.db",
        "*.pyc", "*.pyo", "*.swp", ".vscode", ".idea", "node_modules",
        ".gitignore", ".gitmodules", ".hg", ".svn", ".bzr"
    ]
    
    # Allowed file extensions for sending
    allowed_file_extensions: Optional[List[str]] = None  # None means all extensions
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
