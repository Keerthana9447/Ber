from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.preflight import run_preflight

router = APIRouter(tags=["health"])


@router.get("/health", name="health")
def health() -> dict[str, str]:
    """Liveness only: the process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/health/ready", name="health_ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: database reachable and schema at the expected migration head.
    Public, so it returns only short codes; details are written to the server log."""
    result = run_preflight(request.app.state.engine)
    scheduler = getattr(request.app.state, "scheduler_status", {"consecutive_failures": 0})
    body = {
        "status": "ready" if result.ok else "not_ready",
        "problems": result.codes,
        "notification_scheduler_failing": scheduler.get("consecutive_failures", 0) > 0,
    }
    return JSONResponse(status_code=200 if result.ok else 503, content=body)
