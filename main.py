from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from database.database import engine
from models.base import Base
from models.game import Game
from routers import health
from routers.engine import router as engine_router
from routers.games import router as games_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the database and create tables
    async with engine.begin() as conn:
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
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)