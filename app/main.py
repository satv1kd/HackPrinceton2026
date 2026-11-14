"""FastAPI entry point. Run with `make dev` (or `./run.sh`)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.deps import get_store
from app.routers import phrases
from app.services.audio import ffmpeg_available

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_store()  # creates the DATA_DIR layout + empty phrases.json on first run
    yield


app = FastAPI(title="Heard", lifespan=lifespan)
app.include_router(phrases.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "ffmpeg": ffmpeg_available(),
        "feature_extractor": settings.FEATURE_EXTRACTOR,
        "elevenlabs_key_present": bool(settings.ELEVENLABS_API_KEY),
    }
