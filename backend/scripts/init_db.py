"""Create the MySQL database (if missing) and all tables.

Usage (from backend/):  python scripts/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402

if __name__ == "__main__":
    url = make_url(get_settings().database_url)
    print(f"Connecting to {url.render_as_string(hide_password=True)}")
    try:
        init_db()
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        print("Check that MySQL is running and DATABASE_URL in .env is correct.")
        sys.exit(1)
    print("Tables:", ", ".join(sorted(inspect(engine).get_table_names())))
    print("Database ready.")
