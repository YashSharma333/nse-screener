import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def configure_logging() -> None:
    """Sets up root logging to write to both the terminal and a rotating logbook file."""
    
    # Ensure the logs directory exists
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "screener.log"

    # Define the log message structure (includes file name and line number for easy fixes)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. File Handler: Writes to screener.log. Max size 5MB, keeps the 3 most recent files.
    file_handler = RotatingFileHandler(
        filename=log_file, 
        maxBytes=5_000_000, 
        backupCount=3
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG) # Log everything to the file

    # 2. Console Handler: Prints to your VS Code terminal
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO) # Only log INFO and above to the terminal to avoid noise

    # Apply to the root logger
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
        force=True # Overrides any existing logging configs (e.g., from Streamlit)
    )