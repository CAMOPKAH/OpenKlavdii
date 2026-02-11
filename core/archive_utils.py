"""
Archive creation utilities for OpenKlavdii bot.
Creates ZIP archives for sending multiple files via Telegram.
"""
import zipfile
import asyncio
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging
from datetime import datetime

logger = logging.getLogger("opencode_bot")


class ArchiveCreator:
    """Create ZIP archives for session files."""
    
    # Telegram document size limit: 50MB (leave 5MB margin for safety)
    MAX_ARCHIVE_SIZE = 45 * 1024 * 1024  # 45 MB
    
    @staticmethod
    async def create_session_archive(
        session_folder: Path, 
        file_paths: List[str],
        archive_name: Optional[str] = None
    ) -> Tuple[Optional[BytesIO], str, int]:
        """
        Create ZIP archive of session files in memory.
        
        Args:
            session_folder: Root folder containing files
            file_paths: List of relative file paths to include
            archive_name: Custom archive name (optional)
            
        Returns:
            Tuple of (archive_buffer, archive_name, file_count) or (None, "", 0) on error
        """
        if not file_paths:
            logger.warning("No files to archive")
            return None, "", 0
        
        # Generate archive name if not provided
        if not archive_name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"session_{session_folder.name}_{timestamp}.zip"
        
        zip_buffer = BytesIO()
        files_added = 0
        total_size = 0
        
        try:
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for rel_path in file_paths:
                    abs_path = session_folder / rel_path
                    
                    # Check if file exists
                    if not abs_path.exists():
                        logger.warning(f"File not found, skipping: {rel_path}")
                        continue
                    
                    # Check file size
                    file_size = abs_path.stat().st_size
                    if file_size > ArchiveCreator.MAX_ARCHIVE_SIZE:
                        logger.warning(f"File too large ({file_size} bytes), skipping: {rel_path}")
                        continue
                    
                    # Estimate archive size (approximate compression ratio 2:1)
                    estimated_archive_size = total_size + (file_size // 2)
                    if estimated_archive_size > ArchiveCreator.MAX_ARCHIVE_SIZE:
                        logger.warning(f"Archive would exceed size limit, skipping remaining files")
                        break
                    
                    # Add file to archive
                    try:
                        zipf.write(abs_path, rel_path)
                        files_added += 1
                        total_size += file_size
                        logger.debug(f"Added file to archive: {rel_path} ({file_size} bytes)")
                    except Exception as e:
                        logger.error(f"Failed to add file {rel_path} to archive: {e}")
                        continue
            
            if files_added == 0:
                logger.warning("No files were added to archive")
                return None, "", 0
            
            # Get final archive size
            zip_buffer.seek(0, 2)  # Seek to end
            archive_size = zip_buffer.tell()
            zip_buffer.seek(0)  # Seek back to start
            
            logger.info(f"Created archive '{archive_name}' with {files_added} files, size: {archive_size} bytes")
            return zip_buffer, archive_name, files_added
            
        except Exception as e:
            logger.error(f"Failed to create archive: {e}")
            return None, "", 0
    
    @staticmethod
    def get_archive_size(zip_buffer: BytesIO) -> int:
        """Get archive size in bytes without consuming buffer."""
        current_pos = zip_buffer.tell()
        zip_buffer.seek(0, 2)  # Seek to end
        size = zip_buffer.tell()
        zip_buffer.seek(current_pos)  # Restore position
        return size
    
    @staticmethod
    async def create_individual_files_list(
        session_folder: Path, 
        file_paths: List[str],
        max_files: int = 10
    ) -> List[Tuple[Path, str]]:
        """
        Prepare individual files for sending.
        
        Args:
            session_folder: Root folder containing files
            file_paths: List of relative file paths
            max_files: Maximum number of files to prepare
            
        Returns:
            List of tuples (absolute_path, relative_path)
        """
        files_to_send = []
        
        for rel_path in file_paths[:max_files]:
            abs_path = session_folder / rel_path
            
            if not abs_path.exists():
                logger.warning(f"File not found: {rel_path}")
                continue
            
            file_size = abs_path.stat().st_size
            if file_size > ArchiveCreator.MAX_ARCHIVE_SIZE:
                logger.warning(f"File too large ({file_size} bytes), skipping: {rel_path}")
                continue
            
            files_to_send.append((abs_path, rel_path))
        
        return files_to_send
    
    @staticmethod
    def format_file_list_for_display(
        files: Dict[str, List[str]],
        session_folder: Path,
        max_display: int = 10
    ) -> str:
        """Format file list for display in Telegram message."""
        created = files.get("created", [])
        modified = files.get("modified", [])
        all_files = files.get("all", [])
        
        if not all_files:
            return ""
        
        # Header
        result = f"📁 *Созданные/изменённые файлы ({len(all_files)}):*\n\n"
        
        # Created files section
        if created:
            result += f"**Созданы ({len(created)}):**\n"
            for i, rel_path in enumerate(created[:max_display]):
                filepath = session_folder / rel_path
                size = filepath.stat().st_size if filepath.exists() else 0
                size_str = ArchiveCreator._format_size(size)
                result += f"• `{rel_path}` - {size_str}\n"
            
            if len(created) > max_display:
                result += f"*...и ещё {len(created) - max_display} созданных файлов*\n"
            result += "\n"
        
        # Modified files section
        if modified:
            result += f"**Изменены ({len(modified)}):**\n"
            for i, rel_path in enumerate(modified[:max_display]):
                filepath = session_folder / rel_path
                size = filepath.stat().st_size if filepath.exists() else 0
                size_str = ArchiveCreator._format_size(size)
                result += f"• `{rel_path}` - {size_str}\n"
            
            if len(modified) > max_display:
                result += f"*...и ещё {len(modified) - max_display} изменённых файлов*\n"
            result += "\n"
        
        # Total summary
        total_size = sum((session_folder / rel_path).stat().st_size 
                        for rel_path in all_files 
                        if (session_folder / rel_path).exists())
        
        result += f"**Всего:** {len(all_files)} файлов, {ArchiveCreator._format_size(total_size)}\n\n"
        
        # Send method indication
        if len(all_files) <= 10:
            result += "Отправляю файлы по отдельности..."
        else:
            result += f"Создаю архив ({len(all_files)} файлов)..."
        
        return result
    
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} КБ"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} МБ"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} ГБ"