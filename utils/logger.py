import logging
import sys
import os
from logging.handlers import RotatingFileHandler

def setup_logger():
    logger = logging.getLogger("opencode_bot")
    logger.setLevel(logging.DEBUG)
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler for bot.log
    log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot.log")
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # File handler for debug log
    debug_log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot_debug.log")
    debug_handler = RotatingFileHandler(debug_log_file, maxBytes=10*1024*1024, backupCount=2)
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)
    logger.addHandler(debug_handler)
    
    return logger
