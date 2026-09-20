from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .routers import auth, youtube, editor, clipper, transcript, jobs, files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clip_studio.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Clip Studio database...")
    await init_db()
    logger.info("Clip Studio database ready.")
    yield


app = FastAPI(
    title="Clip Studio AI API",
    description="Backend API for AI Clipper, Interactive Timeline Video Editor, and Whisper Transcriber.",
    version="1.0.0",
    lifespan=lifespan,
)

import os
from dotenv import load_dotenv

load_dotenv()

# Enable CORS for frontend with explicit allowed origins
raw_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000",
)
allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router)
app.include_router(youtube.router)
app.include_router(editor.router)
app.include_router(clipper.router)
app.include_router(transcript.router)
app.include_router(jobs.router)
app.include_router(files.router)


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "Clip Studio AI",
        "version": "1.0.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
