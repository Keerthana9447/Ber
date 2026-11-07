from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", name="health")
def health() -> dict[str, str]:
    return {"status": "ok"}
