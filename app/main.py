from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="Automation Agent API", version="0.1.0")
app.include_router(health_router)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("api_started")

