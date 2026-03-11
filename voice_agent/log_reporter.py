"""Отправляет события Маскота в Django API."""
import logging
import os

import aiohttp

DJANGO_URL = os.environ.get("DJANGO_URL", "https://baza.business-pad.com")
MASTER_API_KEY = os.environ.get("MASTER_API_KEY", "")
logger = logging.getLogger(__name__)


async def report(room: str, event: str, text: str = "", speaker: str = ""):
    if not MASTER_API_KEY:
        return
    url = f"{DJANGO_URL.rstrip('/')}/api/mascot-log/"
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                url,
                json={"room": room, "event": event, "text": text, "speaker": speaker},
                headers={"X-Agent-Key": MASTER_API_KEY},
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception as e:
        logger.debug("log_reporter error: %s", e)
