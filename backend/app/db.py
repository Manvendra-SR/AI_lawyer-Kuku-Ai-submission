"""SQLAlchemy engine / session setup."""
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(_settings.database_url, pool_pre_ping=True, pool_recycle=3600, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_database_if_missing() -> str:
    """Create the MySQL database named in DATABASE_URL if it does not exist. Returns the db name."""
    url = make_url(_settings.database_url)
    db_name = url.database
    if url.get_backend_name() != "mysql" or not db_name:
        return db_name or ""
    server_engine = create_engine(url.set(database=""), future=True)
    with server_engine.connect() as conn:
        conn.exec_driver_sql(
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        conn.commit()
    server_engine.dispose()
    return db_name


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    create_database_if_missing()
    Base.metadata.create_all(bind=engine)
