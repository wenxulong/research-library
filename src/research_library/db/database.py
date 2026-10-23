"""配置数据库连接、会话和 FastAPI 数据库依赖。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from research_library.core.config import settings, require_value

DATABASE_URL = require_value("DATABASE_URL", settings.database_url)


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有 SQLAlchemy 数据库模型的共同基类。"""
    pass

def get_db():
    """为一次请求提供数据库会话，并在结束后自动关闭。"""
    with SessionLocal() as db:
        yield db
