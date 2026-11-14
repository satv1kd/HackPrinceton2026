"""Call install_tasks_5_8 after task 1 has constructed the shared services."""
import asyncio
import logging
from contextlib import asynccontextmanager

from starlette.concurrency import run_in_threadpool

from app.routers import feedback, recognize
from app.services.attempts import AttemptStore
from app.services.feedback import FeedbackStore
from app.services.matcher import DTWMatcher
from app.services.recognition import RecognitionService


def install_tasks_5_8(app, *, config, phrase_store, preprocessor, extractor,
                      feature_cache, data_dir='data', audio_errors=()):
    """Call once before server startup. Keeps task 1's lifespan intact."""
    if getattr(app.state, 'tasks_5_8_installed', False):
        raise RuntimeError('Tasks 5–8 already installed')
    attempts = AttemptStore(data_dir)
    feedback_store = FeedbackStore(data_dir, phrase_store, attempts, feature_cache,
        max_samples=config.MAX_SAMPLES_PER_PHRASE,
        undo_seconds=getattr(config, 'UNDO_WINDOW_SECONDS', 10))
    app.state.recognition_service = RecognitionService(preprocessor, extractor,
        DTWMatcher(config, phrase_store, feature_cache), attempts, audio_errors)
    app.state.feedback_store = feedback_store
    app.include_router(recognize.router)
    app.include_router(feedback.router)
    previous_lifespan = app.router.lifespan_context

    async def maintain(stop):
        while not stop.is_set():
            try:
                await run_in_threadpool(feedback_store.maintenance)
            except Exception:
                logging.getLogger(__name__).exception('Attempt maintenance failed')
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            await run_in_threadpool(feedback_store.maintenance)
            stop = asyncio.Event()
            task = asyncio.create_task(maintain(stop))
            try:
                yield state
            finally:
                stop.set()
                await task

    app.router.lifespan_context = lifespan
    app.state.tasks_5_8_installed = True
