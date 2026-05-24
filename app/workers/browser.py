import asyncio

from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    logger.info("browser_worker_started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
