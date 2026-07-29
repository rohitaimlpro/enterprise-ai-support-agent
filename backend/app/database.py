"""
SQLAlchemy engine/session setup.

We use the classic "create_all on startup" approach instead of Alembic
migrations to keep this project approachable. In a real production app
you'd add Alembic once the schema stabilizes -- see README "Next steps".
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
