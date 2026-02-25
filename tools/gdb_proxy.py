#!/usr/bin/env python3
"""TCP proxy that logs all GDB RSP traffic between a client and server.

Usage:
    python tools/gdb_proxy.py <listen_port> <target_port>

Example:
    # JLinkGDBServer on port 2331, proxy on port 3333
    python tools/gdb_proxy.py 3333 2331

    # Then point your GDB client at localhost:3333
    # All traffic is logged to /tmp/gdb_rsp_traffic.log and printed to terminal
"""

import asyncio
import sys
import time

LOG_FILE = "/tmp/gdb_rsp_traffic.log"


def log(direction: str, data: bytes) -> None:
    text = data.decode("ascii", errors="replace")
    ts = f"{time.time():.3f}"
    line = f"{ts} {direction} {text}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


async def pipe(label: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            log(label, data)
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_port: int,
) -> None:
    print(f"[proxy] Client connected, forwarding to localhost:{target_port}")
    try:
        server_reader, server_writer = await asyncio.open_connection("127.0.0.1", target_port)
    except OSError as e:
        print(f"[proxy] Cannot connect to target port {target_port}: {e}")
        client_writer.close()
        return

    t1 = asyncio.create_task(pipe("GDB>>>", client_reader, server_writer))
    t2 = asyncio.create_task(pipe("<<<GDB", server_reader, client_writer))
    await asyncio.gather(t1, t2, return_exceptions=True)
    print("[proxy] Connection closed")


async def main(listen_port: int, target_port: int) -> None:
    with open(LOG_FILE, "w") as f:
        f.write(f"# GDB RSP proxy log — listening on {listen_port}, forwarding to {target_port}\n")

    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, target_port),
        "127.0.0.1",
        listen_port,
    )
    print(f"[proxy] Listening on localhost:{listen_port} → localhost:{target_port}")
    print(f"[proxy] Log file: {LOG_FILE}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <listen_port> <target_port>")
        sys.exit(1)
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
