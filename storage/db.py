from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # SQLite multi-thread safety
)
Session = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass
