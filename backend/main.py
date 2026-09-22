from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import init_db
from .utils.storage import require_production_storage
from .routers import admin, auth, youtube, editor, clipper, transcript, jobs, files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clip_studio.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Clip Studio database...")
    require_production_storage()
    await init_db()
    logger.info("Recovering jobs interrupted by restart...")
    from .worker import recover_stuck_jobs, cleanup_finished_jobs
    recovered = await recover_stuck_jobs()
    if recovered:
        logger.info("Recovered %d stuck job(s).", recovered)
    removed = await cleanup_finished_jobs(max_age_hours=float(os.getenv("OUTPUT_RETENTION_HOURS", "24")))
    logger.info("Output retention cleanup removed %d artifact(s).", removed)
    logger.info("Clip Studio database ready.")
    yield


app = FastAPI(
    title="Clip Studio AI API",
    description="Backend API for AI Clipper, Interactive Timeline Video Editor, and Whisper Transcriber.",
    version="1.1.0",
    lifespan=lifespan,
)

import os as _os  # noqa: E402  (used below for CORS/env config)

load_dotenv_needed = None  # dotenv loaded in backend.auth on import

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Enable CORS for frontend with explicit allowed origins
raw_origins = _os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000",
)
allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Register routers
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(youtube.router)
app.include_router(editor.router)
app.include_router(clipper.router)
app.include_router(transcript.router)
app.include_router(jobs.router)
app.include_router(files.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline hardening headers (CODE_REVIEW.md finding M7)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    if _os.getenv("ENVIRONMENT", "development").lower().strip() == "production":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "Clip Studio AI",
        "version": "1.1.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
