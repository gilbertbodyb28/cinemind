#!/usr/bin/env python3
"""localhost:8001 on the Mac, served by CineMind on the NAS.

CineMind moved to the NAS (HANDOFF.md omgång 7) and the Mac app was stopped, but
http://localhost:8001 is still the address the AniList and Google sign-ins
return to, what CineMind.app opens and what bookmarks point at - so all of them
ended in ERR_CONNECTION_REFUSED. This forwards the Mac's loopback port 8001 to
the NAS, byte for byte (plain TCP, so the page, the API and cookies behave as if
CineMind still ran locally). It listens on the loopback addresses only; nothing on
the LAN can reach it. Holding the port also keeps the old Mac backend, with its own
stale database, from ever starting next to the NAS copy.

    CINEMIND_NAS=192.168.50.94:8001 python3 scripts/nas_forward.py

Installed as a LaunchAgent by scripts/install_nas_forward.sh.
"""

import asyncio
import logging
import os

# Both loopbacks: a browser may try ::1 for "localhost" first.
LISTEN_HOSTS = [h for h in os.environ.get("CINEMIND_FORWARD_HOST", "127.0.0.1,::1").split(",") if h]
LISTEN_PORT = int(os.environ.get("CINEMIND_FORWARD_PORT", "8001"))
TARGET_HOST, _, TARGET_PORT = os.environ.get("CINEMIND_NAS", "192.168.50.94:8001").partition(":")
TARGET_PORT = int(TARGET_PORT or "8001")
CONNECT_TIMEOUT = 10
CHUNK = 64 * 1024


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(TARGET_HOST, TARGET_PORT), CONNECT_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as exc:
        logging.warning("NAS %s:%s not reachable: %s", TARGET_HOST, TARGET_PORT, exc.__class__.__name__)
        client_writer.close()
        return
    await asyncio.gather(pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer))


async def main() -> None:
    server = await asyncio.start_server(handle, LISTEN_HOSTS, LISTEN_PORT)
    logging.info("forwarding %s port %s -> %s:%s", ",".join(LISTEN_HOSTS), LISTEN_PORT, TARGET_HOST, TARGET_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
