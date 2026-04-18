from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from database.database import engine
from models.base import Base
from models.game import Game
from models.analysis import Analysis
from routers import health
from routers.engine import router as engine_router
from routers.games import router as games_router
from routers.analysis import router as analysis_router
from ws.game import router as ws_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the database and create tables
    async with engine.begin() as conn:
        # Drop and recreate the analysis table to pick up new columns
        # (analysis data is re-generatable, so this is safe)
        # TODO: Remove this after the first successful deployment with new schema
        await conn.run_sync(Analysis.__table__.drop, checkfirst=True)
        await conn.run_sync(Base.metadata.create_all)
    
    yield
    # Cleanup will go here later
    await engine.dispose()

app = FastAPI(title="Chess Coach API", lifespan=lifespan)

# Allow requests from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health.router)
app.include_router(engine_router)
app.include_router(games_router, prefix="/api/v1")
app.include_router(analysis_router, prefix="/api/v1")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)