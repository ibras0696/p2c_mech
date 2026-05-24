import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.integrations.platform_ws.p2c_socket import (
    P2CSocketClient,
    P2CSocketConfig,
    build_cookie_header,
)

configure_logging()
logger = get_logger(__name__)


async def log_business_message(message: str) -> None:
    logger.info("p2c_socket_business_message", extra={"message_prefix": message[:80]})


async def main() -> None:
    settings = get_settings()
    cookie_header = build_cookie_header(
        raw_cookie_header=settings.platform_cookie_header,
        access_token=settings.platform_access_token,
        cf_bm_cookie=settings.platform_cf_bm_cookie,
    )
    client = P2CSocketClient(
        P2CSocketConfig(
            url=settings.platform_ws_url,
            cookie_header=cookie_header,
        ),
        on_message=log_business_message,
    )
    await client.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
