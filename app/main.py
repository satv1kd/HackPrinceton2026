"""FastAPI entry point. Run with `make dev` (or `./run.sh`)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.deps import get_store
from app.routers import health, phrases, preferences, send, tts
from app.services.health import HealthChecker
from app.services.tts import TTSService
from app.task_integration import foundation_services, install_tasks_5_8

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store()  # creates the DATA_DIR layout + empty phrases.json on first run
    settings = get_settings()
    # Built once: the TTS service holds a cache directory and the health
    # checker holds settings, and both are consulted on nearly every request.
    app.state.tts_service = TTSService.from_settings(settings)
    app.state.health_checker = HealthChecker(settings)
    yield


app = FastAPI(title="Heard", lifespan=lifespan)

# The app is reached through a cloudflared/ngrok tunnel during development
# (iPhone Safari only grants microphone access over HTTPS), so the browser's
# origin is a throwaway tunnel hostname that changes on every restart.
# Single user, no auth, no cookies to steal - see docs/https-tunnel.md.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(phrases.router)
app.include_router(tts.router)
app.include_router(send.router)
app.include_router(health.router)
app.include_router(preferences.router)
install_tasks_5_8(app, services_factory=foundation_services)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
