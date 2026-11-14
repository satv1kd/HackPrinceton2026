"""Health endpoint (Task 15)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app.services.health import HealthChecker

router = APIRouter(prefix="/api", tags=["health"])


def checker_for(request: Request) -> HealthChecker:
    checker = getattr(request.app.state, "health_checker", None)
    if checker is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(status_code=500, detail="health checker is not configured")
    return checker


@router.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    """Always answers 200, even when checks fail.

    A monitoring endpoint that 503s tells you less than one that returns the
    detail of what is wrong, and the UI status dot needs the detail.
    """
    return await checker_for(request).check_all()
