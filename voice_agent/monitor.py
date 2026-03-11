"""Room dispatch monitor — диспатчит Маскота только в комнаты с with_mascot=True."""
import asyncio
import logging
import os

import aiohttp
from livekit import api

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LK_URL = os.environ["LIVEKIT_URL"].replace("ws://", "http://").replace("wss://", "https://")
LK_API_KEY = os.environ["LIVEKIT_API_KEY"]
LK_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
AGENT_NAME = os.environ.get("AGENT_NAME", "Маскот")
POLL_INTERVAL = int(os.environ.get("MONITOR_POLL_INTERVAL", "5"))
DJANGO_URL = os.environ.get("DJANGO_URL", "").rstrip("/")
MASTER_API_KEY = os.environ.get("MASTER_API_KEY", "")


async def _room_has_mascot(session: aiohttp.ClientSession, room_name: str) -> bool:
    """Проверяет через Django API, нужен ли маскот в данной комнате."""
    if not DJANGO_URL or not MASTER_API_KEY:
        # Нет конфига → не диспатчить маскота
        return False
    url = f"{DJANGO_URL}/api/room-config/{room_name}/"
    try:
        async with session.get(url, headers={"X-Agent-Key": MASTER_API_KEY}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return bool(data.get("with_mascot", False))
    except Exception as e:
        logger.warning("room-config check failed for '%s': %s", room_name, e)
    return False


async def main():
    dispatched: set[str] = set()
    logger.info("Room monitor started. Watching for rooms to dispatch '%s'...", AGENT_NAME)

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with api.LiveKitAPI(LK_URL, LK_API_KEY, LK_API_SECRET) as lk:
                    rooms_resp = await lk.room.list_rooms(api.ListRoomsRequest())
                    open_names: set[str] = set()

                    for room in rooms_resp.rooms:
                        open_names.add(room.name)
                        if room.num_participants == 0:
                            continue
                        if room.name in dispatched:
                            continue

                        # Проверяем флаг with_mascot через Django API
                        if not await _room_has_mascot(session, room.name):
                            logger.debug("Room '%s': mascot disabled, skipping.", room.name)
                            dispatched.add(room.name)  # не перепроверять каждые 5с
                            continue

                        # Проверяем, нет ли уже диспатча
                        dispatches = await lk.agent_dispatch.list_dispatch(room.name)
                        already = any(d.agent_name == AGENT_NAME for d in dispatches)
                        if not already:
                            await lk.agent_dispatch.create_dispatch(
                                api.CreateAgentDispatchRequest(
                                    room=room.name,
                                    agent_name=AGENT_NAME,
                                )
                            )
                            logger.info("✅ Dispatched '%s' → room '%s'", AGENT_NAME, room.name)

                        dispatched.add(room.name)

                    # Чистим закрытые комнаты из кэша
                    dispatched &= open_names

            except Exception as e:
                logger.warning("Monitor error: %s", e, exc_info=True)

            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
