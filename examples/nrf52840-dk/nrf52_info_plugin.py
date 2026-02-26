"""Plugin to read nRF52 device identity from FICR registers."""

import struct

from mcp.types import Tool

from dbgprobe_mcp_server.helpers import _ok
from dbgprobe_mcp_server.state import ProbeState

META = {
    "description": "Read nRF52 unique device ID from FICR",
}

TOOLS = [
    Tool(
        name="nrf52_info.device_id",
        description=(
            "Read the nRF52 unique 64-bit device ID from FICR registers (0x10000060 and 0x10000064)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
]


async def handle_device_id(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    # FICR.DEVICEID[0] at 0x10000060, DEVICEID[1] at 0x10000064
    data = await backend.mem_read(0x10000060, 8)
    device_id_0 = struct.unpack("<I", data[0:4])[0]
    device_id_1 = struct.unpack("<I", data[4:8])[0]
    device_id = (device_id_1 << 32) | device_id_0

    return _ok(
        session_id=args["session_id"],
        device_id=f"0x{device_id:016X}",
        device_id_0=f"0x{device_id_0:08X}",
        device_id_1=f"0x{device_id_1:08X}",
    )


HANDLERS = {
    "nrf52_info.device_id": handle_device_id,
}
