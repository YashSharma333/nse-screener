import os
from pathlib import Path

def create_project_structure():
    """Generates the production directory tree and files for the NSE screener."""
    
    base_dir = Path.cwd()
    
    directories = [
        "config",
        "data/raw",
        "data/processed",
        "src/db",
        "src/ingestion",
        "src/etl",
        "src/computation",
        "ui/components",
        "ui/pages",
        "tests"
    ]
    
    files = [
        "config/settings.py",
        "src/__init__.py",
        "src/db/__init__.py",
        "src/db/models.py",
        "src/db/session.py",
        "src/ingestion/__init__.py",
        "src/ingestion/downloader.py",
        "src/etl/__init__.py",
        "src/etl/pipeline.py",
        "src/computation/__init__.py",
        "src/computation/indicators.py",
        "ui/app.py",
        "ui/components/__init__.py",
        "ui/pages/1_dashboard.py",
        ".env.example",
        ".gitignore",
        "Dockerfile",
        "requirements.txt"
    ]

    print("Initializing project structure...")

    for directory in directories:
        dir_path = base_dir / directory
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {dir_path}")

    for file in files:
        file_path = base_dir / file
        file_path.touch(exist_ok=True)
        print(f"Created file: {file_path}")

    print("\nProject scaffolding complete. You are ready to build.")

if __name__ == "__main__":
    create_project_structure()