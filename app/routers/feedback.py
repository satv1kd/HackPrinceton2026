from fastapi import APIRouter, HTTPException, Request

from app.models.recognition import FeedbackRequest, UndoRequest
from app.services.feedback import FeedbackConflict, IntegrationRequired

router = APIRouter(prefix='/api', tags=['feedback'])


def store_for(request):
    store = getattr(request.app.state, 'feedback_store', None)
    if store is None:
        raise HTTPException(503, 'Feedback services have not been connected')
    return store


def invoke(operation, *args):
    try:
        return operation(*args)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FeedbackConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except IntegrationRequired as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post('/feedback')
def feedback(body: FeedbackRequest, request: Request):
    label = body.label.model_dump() if body.label else None
    response = invoke(store_for(request).record, str(body.attempt_id), body.correct, label)
    # Every confirmed attempt is training data for the sound profile; retrain
    # in the background once enough new examples of a sound have built up.
    try:
        from app.deps import get_profile_trainer
        triggered = get_profile_trainer().on_feedback()
    except Exception:  # learning must never break saving the response
        import logging
        logging.getLogger(__name__).exception('could not check for a profile retrain')
        triggered = False
    return {**response, 'retrain_triggered': bool(triggered)}


@router.post('/feedback/undo')
def undo(body: UndoRequest, request: Request):
    invoke(store_for(request).undo, str(body.attempt_id), str(body.event_id) if body.event_id else None)
    return {'ok': True}
