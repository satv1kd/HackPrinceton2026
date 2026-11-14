"""Connect recognition/feedback to the foundation's shared service instances."""
import asyncio
import logging
from contextlib import asynccontextmanager

from starlette.concurrency import run_in_threadpool

from app.routers import feedback, recognize
from app.services.attempts import AttemptStore
from app.services.feedback import FeedbackStore
from app.services.matcher import DTWMatcher
from app.services.recognition import RecognitionService


def foundation_services():
    from app.config import get_settings
    from app.deps import get_feature_cache, get_preprocessor, get_store
    config = get_settings()
    cache = get_feature_cache()
    return dict(config=config, phrase_store=get_store(), preprocessor=get_preprocessor(),
                extractor=cache.extractor, feature_cache=cache, data_dir=config.DATA_DIR)


def install_tasks_5_8(app, *, services_factory=None, **services):
    """Register once; instantiate services at startup so settings and tests stay isolated."""
    if getattr(app.state, 'tasks_5_8_installed', False):
        raise RuntimeError('Tasks 5–8 already installed')
    app.include_router(recognize.router)
    app.include_router(feedback.router)
    previous_lifespan = app.router.lifespan_context

    async def maintain(stop, store):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                try:
                    await run_in_threadpool(store.maintenance)
                except Exception:
                    logging.getLogger(__name__).exception('Attempt maintenance failed')

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            dependencies = services_factory() if services_factory else services
            config = dependencies['config']
            phrase_store = dependencies['phrase_store']
            cache = dependencies['feature_cache']
            data_dir = dependencies.get('data_dir', config.DATA_DIR if hasattr(config, 'DATA_DIR') else 'data')
            attempts = AttemptStore(data_dir)
            store = FeedbackStore(data_dir, phrase_store, attempts, cache,
                max_samples=config.MAX_SAMPLES_PER_PHRASE,
                undo_seconds=config.UNDO_WINDOW_SECONDS if hasattr(config, 'UNDO_WINDOW_SECONDS') else 10)
            application.state.feedback_store = store
            application.state.recognition_service = RecognitionService(
                dependencies['preprocessor'], dependencies['extractor'],
                DTWMatcher(config, phrase_store, cache), attempts)
            await run_in_threadpool(store.maintenance)
            stop = asyncio.Event()
            task = asyncio.create_task(maintain(stop, store))
            try:
                yield state
            finally:
                stop.set()
                await task

    app.router.lifespan_context = lifespan
    app.state.tasks_5_8_installed = True
