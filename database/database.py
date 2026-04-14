import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(dotenv_path=env_path)

# Render sets 'RENDER' to 'true' automatically
is_production = os.getenv("RENDER") == "true"
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    if is_production:
        raise ValueError("\n\n❌ CRITICAL: 'DATABASE_URL' environment variable is missing!\nGo to the 'Environment' tab in your Render Web Service dashboard and add it, then redeploy.\n\n")

    print("WARNING: DATABASE_URL environment variable is not set. Falling back to local SQLite.")
    DATABASE_URL = "sqlite+aiosqlite:///./chess_coach.db"

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Dependency for FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
