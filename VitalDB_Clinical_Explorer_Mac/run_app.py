"""Convenience entry point: ``.venv\\Scripts\\python.exe run_app.py``."""
import sys

from vitaldb_explorer.app import main

if __name__ == "__main__":
    sys.exit(main())
