"""
Utilities for managing session files and logging
"""
import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger("opencode_bot")

def get_session_folder(session_id: str) -> Path:
    """Get or create session folder"""
    folder = Path(f"work_place/{session_id}")
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def log_to_proc_md(session_id: str, request: str, response: str, thinking_blocks: Optional[List[str]] = None):
    """Log request and response to proc.md in session folder"""
    folder = get_session_folder(session_id)
    proc_file = folder / "proc.md"
    
    timestamp = datetime.now().isoformat()
    
    log_entry = f"""
## {timestamp}

### Request
```
{request}
```

### Response
```
{response}
```
"""
    
    if thinking_blocks is not None:
        log_entry += "\n### Thinking Blocks\n"
        for i, block in enumerate(thinking_blocks):
            log_entry += f"\n**Block {i+1}**:\n{block}\n"
    
    log_entry += "\n---\n"
    
    # Append to file
    with open(proc_file, 'a', encoding='utf-8') as f:
        f.write(log_entry)
    
    logger.debug(f"Logged to {proc_file}")

def move_file_to_session(session_id: str, filepath: str) -> Optional[Path]:
    """Move a file created by OpenCode to session folder"""
    if not os.path.exists(filepath):
        return None
    
    src = Path(filepath)
    if not src.is_file():
        return None
    
    folder = get_session_folder(session_id)
    dest = folder / src.name
    
    try:
        src.rename(dest)
        logger.info(f"Moved {filepath} to session folder {dest}")
        return dest
    except Exception as e:
        logger.error(f"Failed to move file {filepath}: {e}")
        # Try copy instead
        try:
            import shutil
            shutil.copy2(src, dest)
            src.unlink()
            logger.info(f"Copied {filepath} to session folder {dest}")
            return dest
        except Exception as e2:
            logger.error(f"Failed to copy file {filepath}: {e2}")
            return None

def list_session_files(session_id: str) -> list:
    """List all files in session folder"""
    folder = get_session_folder(session_id)
    if not folder.exists():
        return []
    
    files = []
    for item in folder.iterdir():
        if item.is_file():
            files.append({
                "name": item.name,
                "size": item.stat().st_size,
                "modified": item.stat().st_mtime
            })
    
    return sorted(files, key=lambda x: x["modified"])

def get_file_content(session_id: str, filename: str) -> Optional[str]:
    """Get content of a file from session folder"""
    folder = get_session_folder(session_id)
    filepath = folder / filename
    if not filepath.exists() or not filepath.is_file():
        return None
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read file {filepath}: {e}")
        return None

def save_file_to_session(session_id: str, filename: str, content: str):
    """Save content to a file in session folder"""
    folder = get_session_folder(session_id)
    filepath = folder / filename
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Saved file {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save file {filepath}: {e}")
        return None

def setup_ssh_environment() -> dict:
    """Setup SSH environment for git operations"""
    import os
    env = os.environ.copy()
    
    # Check if SSH key exists and is accessible
    ssh_key_path = os.path.expanduser("~/.ssh/klavdii_bot_deploy")
    if os.path.exists(ssh_key_path):
        # Ensure proper permissions
        try:
            os.chmod(ssh_key_path, 0o600)
        except:
            pass
        
        # Set SSH command to use our specific key
        env["GIT_SSH_COMMAND"] = f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no"
        logger.debug(f"SSH environment configured with key: {ssh_key_path}")
    else:
        logger.warning(f"SSH key not found at {ssh_key_path}")
    
    return env


def publish_to_github(session_id: str, repo_path: str = "../klavdii_work_place") -> Dict[str, Any]:
    """Publish session files to GitHub repository"""
    import subprocess
    import shutil
    from datetime import datetime
    
    logger.info(f"Starting GitHub publish for session: {session_id}")
    session_folder = get_session_folder(session_id)
    if not session_folder.exists():
        logger.error(f"Session folder not found: {session_folder}")
        return {"success": False, "error": "Session folder not found"}
    
    # Check if repo exists
    repo = Path(repo_path)
    repo_exists = repo.exists() and (repo / ".git").exists()
    logger.info(f"Repository path: {repo_path}, exists: {repo_exists}")
    
    if not repo_exists:
        # Try to clone using SSH
        try:
            # Use SSH URL for cloning
            clone_env = setup_ssh_environment()
            subprocess.run(["git", "clone", "git@github.com-klavdii:CAMOPKAH/klavdii_work_place.git", repo_path], 
                         check=True, capture_output=True, text=True, env=clone_env)
            logger.info(f"Cloned repository to {repo_path}")
            repo_exists = True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone repository: {e}")
            return {"success": False, "error": f"Failed to clone repository: {e.stderr}"}
    
    # Create session subdirectory in repo (inside work_place for consistency)
    target_dir = repo / "work_place" / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy files
    copied_files = []
    for item in session_folder.iterdir():
        if item.is_file():
            dest = target_dir / item.name
            shutil.copy2(item, dest)
            copied_files.append(item.name)
            logger.info(f"Copied {item.name} to {dest}")
    
    if not copied_files:
        logger.warning(f"No files to publish in session: {session_id}")
        return {"success": False, "error": "No files to publish"}
    
    logger.info(f"Copied {len(copied_files)} files: {copied_files}")
    
    # Git operations
    original_cwd = None
    try:
        # Change to repo directory
        original_cwd = Path.cwd()
        os.chdir(repo)
        logger.info(f"Changed directory to: {repo}")
        
        # Setup SSH environment for git operations
        ssh_env = setup_ssh_environment()
        
        # Configure git user if not configured
        try:
            subprocess.run(["git", "config", "user.email", "klavdii-bot@example.com"], 
                         capture_output=True, text=True, check=False, env=ssh_env)
            subprocess.run(["git", "config", "user.name", "Klavdii Bot"], 
                         capture_output=True, text=True, check=False, env=ssh_env)
        except Exception as e:
            logger.warning(f"Could not configure git user: {e}")
        
        # Check if repository has any commits
        try:
            result = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], 
                                   capture_output=True, text=True, env=ssh_env)
            has_commits = result.returncode == 0
        except Exception:
            has_commits = False
        
        # If no commits, create initial commit
        if not has_commits:
            # Create README if it doesn't exist
            readme_path = repo / "README.md"
            if not readme_path.exists():
                with open(readme_path, "w") as f:
                    f.write("# Klavdii Work Place\n\nGitHub repository for Klavdii bot session files.\n")
                subprocess.run(["git", "add", "README.md"], capture_output=True, text=True, check=False, env=ssh_env)
            
            # Create initial commit
            subprocess.run(["git", "commit", "-m", "Initial commit - Klavdii Work Place"], 
                         capture_output=True, text=True, check=False, env=ssh_env)
            
            # Determine default branch name (main or master)
            try:
                result = subprocess.run(["git", "branch", "--show-current"], 
                                       capture_output=True, text=True, check=True, env=ssh_env)
                branch_name = result.stdout.strip()
            except Exception:
                # Try to check remote
                try:
                    result = subprocess.run(["git", "remote", "show", "origin"], 
                                           capture_output=True, text=True, check=False, env=ssh_env)
                    if "HEAD branch: main" in result.stdout:
                        branch_name = "main"
                    else:
                        branch_name = "master"
                except Exception:
                    branch_name = "main"
            
            # Create branch if needed
            if not branch_name:
                branch_name = "main"
                subprocess.run(["git", "branch", "-M", branch_name], 
                             capture_output=True, text=True, check=False, env=ssh_env)
        
        # Add session files
        subprocess.run(["git", "add", str(target_dir.relative_to(repo))], 
                     capture_output=True, text=True, check=False, env=ssh_env)
        
        # Commit
        commit_message = f"Add session {session_id} - {datetime.now().isoformat()}"
        commit_result = subprocess.run(["git", "commit", "-m", commit_message], 
                                     capture_output=True, text=True, check=False, env=ssh_env)
        
        if commit_result.returncode != 0:
            # Check if there are changes to commit
            status_result = subprocess.run(["git", "status", "--porcelain"], 
                                         capture_output=True, text=True, check=True, env=ssh_env)
            if not status_result.stdout.strip():
                logger.warning("No changes to commit")
            else:
                logger.warning(f"Git commit failed: {commit_result.stderr}")
        
        # Push to remote
        try:
            # Determine current branch
            branch_result = subprocess.run(["git", "branch", "--show-current"], 
                                         capture_output=True, text=True, check=True, env=ssh_env)
            current_branch = branch_result.stdout.strip() or "main"

            # Pull latest changes to avoid non-fast-forward errors
            logger.info("Pulling latest changes from remote...")
            pull_result = subprocess.run(["git", "pull", "--rebase", "origin", current_branch], 
                                       capture_output=True, text=True, check=False, env=ssh_env)
            if pull_result.returncode != 0:
                logger.warning(f"Git pull failed: {pull_result.stderr}")
                # Try without rebase as fallback
                pull_result = subprocess.run(["git", "pull", "origin", current_branch], 
                                           capture_output=True, text=True, check=False, env=ssh_env)
                if pull_result.returncode != 0:
                    logger.warning(f"Git pull (non-rebase) also failed: {pull_result.stderr}")

            # Push with set-upstream if needed
            push_result = subprocess.run(["git", "push", "-u", "origin", current_branch], 
                                       capture_output=True, text=True, check=False, env=ssh_env)
            
            if push_result.returncode != 0:
                logger.warning(f"Git push with -u failed, trying simple push...")
                # Try simple push as fallback
                push_result = subprocess.run(["git", "push"], 
                                           capture_output=True, text=True, check=False, env=ssh_env)
            
            if push_result.returncode != 0:
                logger.error(f"Git push failed: {push_result.stderr}")
                error_msg = push_result.stderr.strip()
                # Detect authentication errors
                if "could not read Username" in error_msg or "Authentication failed" in error_msg:
                    helpful_msg = (
                        "GitHub authentication failed.\n\n"
                        "Deploy Key is configured but authentication failed.\n\n"
                        "To fix this:\n"
                        "1. Check Deploy Key permissions in GitHub repository Settings → Deploy keys\n"
                        "2. Ensure the key has **write access** enabled\n"
                        "3. Verify SSH key is loaded: ssh-add -l\n"
                        "4. Test SSH connection: ssh -T git@github.com-klavdii\n"
                        "5. Check remote URL: git remote -v (should use git@github.com-klavdii:...)\n\n"
                        "Original error: " + error_msg[:200]
                    )
                    return {"success": False, "error": helpful_msg, "files_copied": copied_files}
                else:
                    return {"success": False, "error": f"Push failed: {error_msg}", "files_copied": copied_files}
        
        except Exception as push_error:
            logger.error(f"Error during git push: {push_error}")
            return {"success": False, "error": f"Push error: {str(push_error)}", "files_copied": copied_files}
        
        logger.info(f"Git push successful for session {session_id}")
        os.chdir(original_cwd)
        
        logger.info(f"Published session {session_id} to GitHub")
        return {"success": True, "files_copied": copied_files, "repo": repo_path}
    
    except Exception as e:
        logger.error(f"Error during git operations: {e}")
        # Try to restore original directory
        if original_cwd is not None:
            try:
                os.chdir(str(original_cwd))
            except:
                pass
        return {"success": False, "error": str(e), "files_copied": copied_files}