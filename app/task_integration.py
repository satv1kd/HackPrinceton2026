"""Connect recognition/feedback to the foundation's shared service instances."""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from starlette.concurrency import run_in_threadpool

from app.routers import feedback, recognize
from app.services.attempts import AttemptStore
from app.services.feedback import FeedbackStore
from app.services.matcher import DTWMatcher
from app.services.recognition import RecognitionService
from app.services.recognition_stages import PhraseMatchStage, SpeechToTextStage


def foundation_services():
    from app.config import get_settings
    from app.deps import get_feature_cache, get_preprocessor, get_store
    config = get_settings()
    from app.deps import get_preference_store
    cache = get_feature_cache()
    return dict(config=config, phrase_store=get_store(), preprocessor=get_preprocessor(),
                extractor=cache.extractor, feature_cache=cache, data_dir=config.DATA_DIR,
                preference_store=get_preference_store(), extra_stages=build_extra_stages)


def build_extra_stages(dependencies):
    """Recognition stages after the saved-phrase match, in cascade order.

    Phonetic decoder (Task 30), then speech-to-text (Task 19). Each stage
    reports itself unavailable when its optional dependencies are missing,
    so building them here is always safe.
    """
    from app.deps import get_lexicon, get_phoneme_service, get_profile_trainer, get_stt_backend
    from app.services.recognition_stages import PhoneticDecoderStage

    config, phrases = dependencies['config'], dependencies['phrase_store']
    return [
        PhoneticDecoderStage.from_settings(config, get_phoneme_service(), get_profile_trainer(),
                                           get_lexicon, phrases),
        SpeechToTextStage.from_settings(config, get_stt_backend(), phrases, get_lexicon),
    ]


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
            stages = [PhraseMatchStage(DTWMatcher(config, phrase_store, cache), data_dir)]
            extra = dependencies.get('extra_stages')
            stages += list(extra(dependencies) if callable(extra) else extra or [])
            application.state.recognition_service = RecognitionService(
                dependencies['preprocessor'], dependencies['extractor'], attempts, stages,
                preference_store=dependencies.get('preference_store'))
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
