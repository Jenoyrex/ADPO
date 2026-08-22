from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.logging import get_logger, log_extra

router = APIRouter(tags=["health"])
logger = get_logger("adpo.health")


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001 - health check must report, not raise
        # This endpoint is unauthenticated, so the raw exception (which can
        # include connection details) is logged server-side only - never
        # returned to the caller.
        logger.warning("health check database probe failed", extra=log_extra(error=str(exc)))
        db_status = "error"
    return {"status": "ok", "database": db_status}
