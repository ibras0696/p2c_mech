from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.runtime_registry import get_runtime_provider

router = APIRouter(tags=["health"])


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/runtime")
async def runtime_healthcheck() -> JSONResponse:
    provider = get_runtime_provider()
    if provider is None:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "runtime": [], "runtime_count": 0},
        )
    rows = await provider.runtime_statuses()
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "runtime": rows, "runtime_count": len(rows)},
    )
