# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Enrico Santagati

"""Plugin for TFLite Micro — model inspection, data injection, and profiling.

Works with any firmware that exports these ELF symbols:
  - tensor_arena             (uint8_t[])   — the tensor arena buffer
  - <model_symbol>           (uint8_t[])   — TFLite FlatBuffer in flash (configurable)
  - tflm_input_buffer        (int8_t*)     — resolved input data pointer
  - tflm_output_buffer       (int8_t*)     — resolved output data pointer
  - tflm_input_size          (int32_t)     — input element count
  - tflm_output_size         (int32_t)     — output element count
  - tflm_output_scale        (float)       — output quantization scale
  - tflm_output_zero_point   (int32_t)     — output quantization zero point
  - tflm_run_inference       (int32_t)     — write 1 to trigger a single inference
"""

import struct

from mcp.types import Tool

from dbgprobe_mcp_server.elf import resolve_symbol
from dbgprobe_mcp_server.helpers import _err, _ok
from dbgprobe_mcp_server.state import ProbeState

META = {
    "description": "TFLite Micro plugin — inspect models, inject data, read inference results",
}

DEFAULT_MODEL_SYMBOL = "g_micro_speech_quantized_model_data"
DEFAULT_LABELS = ["silence", "unknown", "yes", "no"]


def _parse_labels(args):
    """Parse labels from args, falling back to DEFAULT_LABELS."""
    raw = args.get("labels", "")
    if raw:
        return [label.strip() for label in raw.split(",")]
    return DEFAULT_LABELS


# ---------------------------------------------------------------------------
# Helper: resolve an ELF symbol address
# ---------------------------------------------------------------------------


def _lookup(session, name):
    """Look up a symbol in the attached ELF. Returns (address, size) or None."""
    if session.elf is None:
        return None
    sym = resolve_symbol(session.elf, name)
    if sym is None:
        return None
    return sym.address, sym.size


async def _read_u32(backend, addr):
    data = await backend.mem_read(addr, 4)
    return struct.unpack("<I", data)[0]


async def _read_i32(backend, addr):
    data = await backend.mem_read(addr, 4)
    return struct.unpack("<i", data)[0]


async def _read_f32(backend, addr):
    data = await backend.mem_read(addr, 4)
    return struct.unpack("<f", data)[0]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="tflite_micro.model_info",
        description=(
            "Read TFLite model metadata from flash: size, FlatBuffer identifier, "
            "schema version. Requires ELF attached."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "model_symbol": {
                    "type": "string",
                    "description": (
                        "ELF symbol name for the model data array "
                        "(default: g_micro_speech_quantized_model_data)."
                    ),
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tflite_micro.arena_info",
        description=(
            "Read tensor arena address, total size, and utilization. "
            "Shows how much of the arena is used vs free."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tflite_micro.tensor_info",
        description=(
            "Read input/output tensor buffer addresses, sizes, and "
            "quantization parameters from the running firmware."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tflite_micro.read_output",
        description=(
            "Read the output tensor scores. Returns raw int8 values and "
            "dequantized float scores with predicted class label."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "labels": {
                    "type": "string",
                    "description": (
                        "Comma-separated class labels matching output tensor order "
                        "(default: 'silence,unknown,yes,no'). Must match the number "
                        "of output classes in the model."
                    ),
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tflite_micro.write_input",
        description=(
            "Write data to the input tensor buffer. Provide data as a hex string, "
            "a file path (.bin or .hex), or use 'pattern' for built-in test patterns "
            "(0=silence, 1=noise, 2=yes-like, 3=no-like). Automatically triggers "
            "inference and returns the result unless run_inference=false. "
            "Prefer 'file' over 'data_hex' for large payloads — it reads from disk "
            "and avoids sending 4KB through the LLM context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "file": {
                    "type": "string",
                    "description": (
                        "Path to a .bin (raw bytes) or .hex (hex text) file. "
                        "Much faster than data_hex for real audio features."
                    ),
                },
                "pattern": {
                    "type": "integer",
                    "description": "Test pattern: 0=silence, 1=noise, 2=yes-like, 3=no-like",
                },
                "data_hex": {
                    "type": "string",
                    "description": "Raw hex data to write (must be exactly input_size bytes)",
                },
                "run_inference": {
                    "type": "boolean",
                    "description": "Trigger inference after writing (default: true)",
                },
                "labels": {
                    "type": "string",
                    "description": (
                        "Comma-separated class labels matching output tensor order "
                        "(default: 'silence,unknown,yes,no'). Used when reporting "
                        "inference results."
                    ),
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tflite_micro.infer",
        description=(
            "Trigger a single inference on the current input tensor contents. "
            "Sets tflm_run_inference=1, resumes the target, waits for the "
            "inference to complete, then returns the output scores."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "labels": {
                    "type": "string",
                    "description": (
                        "Comma-separated class labels matching output tensor order "
                        "(default: 'silence,unknown,yes,no')."
                    ),
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tflite_micro.read_input",
        description=(
            "Read a summary of the current input tensor data: min, max, mean, "
            "non-zero count, and first 40 values (one MFCC frame)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tflite_micro.write_pcm",
        description=(
            "Write raw PCM audio to the device and run on-device preprocessing "
            "+ inference. Reads a .wav file from host disk (must be mono, 16kHz, "
            "16-bit, ~1 second), writes 32KB PCM to tflm_pcm_buffer, sets "
            "tflm_run_preprocess=1, resumes target, waits for completion, "
            "and returns the inference result."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "file": {
                    "type": "string",
                    "description": (
                        "Path to a .wav file (mono, 16kHz, 16-bit PCM). "
                        "If shorter than 1s it is zero-padded; if longer it is truncated."
                    ),
                },
                "labels": {
                    "type": "string",
                    "description": (
                        "Comma-separated class labels matching output tensor order "
                        "(default: 'silence,unknown,yes,no'). Used when reporting "
                        "inference results."
                    ),
                },
            },
            "required": ["session_id", "file"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_model_info(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    model_symbol = args.get("model_symbol", DEFAULT_MODEL_SYMBOL)
    sym = _lookup(session, model_symbol)
    if not sym:
        return _err("no_elf", f"ELF not attached or symbol '{model_symbol}' not found")
    model_addr, model_sym_size = sym

    # Read first 24 bytes of FlatBuffer to get header
    header = await backend.mem_read(model_addr, 24)

    # TFLite FlatBuffer: first 4 bytes = root table offset
    # File identifier at offset 4..8 should be "TFL3"
    root_offset = struct.unpack("<I", header[0:4])[0]
    file_id = header[4:8]
    file_id_str = file_id.decode("ascii", errors="replace")

    return _ok(
        model_symbol=model_symbol,
        model_address=f"0x{model_addr:08X}",
        model_size=model_sym_size,
        model_size_kb=f"{model_sym_size / 1024:.1f}",
        flatbuffer_id=file_id_str,
        root_table_offset=root_offset,
        region="flash" if model_addr < 0x20000000 else "ram",
    )


async def handle_arena_info(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    sym = _lookup(session, "tensor_arena")
    if not sym:
        return _err("no_elf", "ELF not attached or tensor_arena symbol not found")
    arena_addr, arena_size = sym

    # Read exact arena usage from firmware global (set by arena_used_bytes())
    used_sym = _lookup(session, "tflm_arena_used")
    if used_sym:
        arena_used = await _read_i32(backend, used_sym[0])
    else:
        arena_used = None

    result = {
        "arena_address": f"0x{arena_addr:08X}",
        "arena_size": arena_size,
        "arena_size_kb": f"{arena_size / 1024:.1f}",
    }

    if arena_used is not None and arena_used > 0:
        result.update(
            {
                "used": arena_used,
                "used_kb": f"{arena_used / 1024:.1f}",
                "free": arena_size - arena_used,
                "utilization_pct": f"{100 * arena_used / arena_size:.1f}",
                "source": "arena_used_bytes()",
            }
        )
    else:
        result["used"] = "unknown (tflm_arena_used not found or not initialized)"

    return _ok(**result)


async def handle_tensor_info(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    results = {}

    # Read input buffer pointer and size
    for prefix, label in [("tflm_input", "input"), ("tflm_output", "output")]:
        buf_sym = _lookup(session, f"{prefix}_buffer")
        size_sym = _lookup(session, f"{prefix}_size")
        if not buf_sym or not size_sym:
            results[label] = "symbol not found"
            continue

        buf_ptr = await _read_u32(backend, buf_sym[0])
        size = await _read_i32(backend, size_sym[0])
        results[label] = {
            "buffer_address": f"0x{buf_ptr:08X}" if buf_ptr else "null (not initialized)",
            "size": size,
        }

    # Read output quantization params
    scale_sym = _lookup(session, "tflm_output_scale")
    zp_sym = _lookup(session, "tflm_output_zero_point")
    if scale_sym and zp_sym:
        scale = await _read_f32(backend, scale_sym[0])
        zp = await _read_i32(backend, zp_sym[0])
        results["output_quantization"] = {
            "scale": scale,
            "zero_point": zp,
        }

    return _ok(**results)


async def handle_read_output(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    buf_sym = _lookup(session, "tflm_output_buffer")
    size_sym = _lookup(session, "tflm_output_size")
    scale_sym = _lookup(session, "tflm_output_scale")
    zp_sym = _lookup(session, "tflm_output_zero_point")

    if not all([buf_sym, size_sym, scale_sym, zp_sym]):
        return _err("no_elf", "Required symbols not found — attach ELF first")

    buf_ptr = await _read_u32(backend, buf_sym[0])
    if buf_ptr == 0:
        return _err("not_initialized", "Output buffer is null — run micro_speech_init() first")

    size = await _read_i32(backend, size_sym[0])
    scale = await _read_f32(backend, scale_sym[0])
    zp = await _read_i32(backend, zp_sym[0])

    # Read raw output bytes
    raw_bytes = await backend.mem_read(buf_ptr, size)
    raw_values = [struct.unpack("<b", bytes([b]))[0] for b in raw_bytes]

    # Dequantize
    scores = [(v - zp) * scale for v in raw_values]

    # Find best
    labels = _parse_labels(args)
    best_idx = scores.index(max(scores))
    best_label = labels[best_idx] if best_idx < len(labels) else f"class_{best_idx}"

    categories = {}
    for i, (raw, score) in enumerate(zip(raw_values, scores, strict=False)):
        label = labels[i] if i < len(labels) else f"class_{i}"
        categories[label] = {"raw_int8": raw, "score": round(score, 4)}

    return _ok(
        predicted_class=best_label,
        confidence=round(max(scores), 4),
        categories=categories,
        quantization={"scale": scale, "zero_point": zp},
    )


async def _trigger_inference(session, backend):
    """Set tflm_run_inference=1, resume, wait for completion, return output."""
    import asyncio

    trigger_sym = _lookup(session, "tflm_run_inference")
    if not trigger_sym:
        return None, "tflm_run_inference symbol not found — rebuild firmware"

    # Write trigger flag
    await backend.mem_write(trigger_sym[0], struct.pack("<i", 1))

    # Resume target
    await backend.go()

    # Wait for inference to complete (flag cleared back to 0)
    # Inference takes ~31ms, poll loop checks every 50ms, so ~100ms max
    for _ in range(20):  # up to 1 second
        await asyncio.sleep(0.05)
        val = await _read_i32(backend, trigger_sym[0])
        if val == 0:
            break

    # Halt to read results
    await backend.halt()

    return "ok", None


async def handle_infer(state: ProbeState, args: dict) -> dict:
    """Trigger inference and return results."""
    session = state.get_session(args["session_id"])
    backend = session.backend

    _status, err = await _trigger_inference(session, backend)
    if err:
        return _err("trigger_failed", err)

    # Read output inline
    return await handle_read_output(state, args)


async def handle_write_input(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    buf_sym = _lookup(session, "tflm_input_buffer")
    size_sym = _lookup(session, "tflm_input_size")

    if not buf_sym or not size_sym:
        return _err("no_elf", "Required symbols not found — attach ELF first")

    buf_ptr = await _read_u32(backend, buf_sym[0])
    if buf_ptr == 0:
        return _err("not_initialized", "Input buffer is null — run micro_speech_init() first")

    input_size = await _read_i32(backend, size_sym[0])

    write_result = None

    if args.get("file"):
        import os

        fpath = args["file"]
        if not os.path.isfile(fpath):
            return _err("file_not_found", f"File not found: {fpath}")
        if fpath.endswith(".hex"):
            with open(fpath) as f:
                data = bytes.fromhex(f.read().strip())
        else:
            with open(fpath, "rb") as f:
                data = f.read()
        if len(data) != input_size:
            return _err("size_mismatch", f"File has {len(data)} bytes, need {input_size}")
        await backend.mem_write(buf_ptr, data)
        write_result = _ok(
            written=len(data),
            buffer_address=f"0x{buf_ptr:08X}",
            source=os.path.basename(fpath),
        )

    elif args.get("data_hex"):
        data = bytes.fromhex(args["data_hex"])
        if len(data) != input_size:
            return _err("size_mismatch", f"Data length {len(data)} != input size {input_size}")
        await backend.mem_write(buf_ptr, data)
        write_result = _ok(
            written=len(data),
            buffer_address=f"0x{buf_ptr:08X}",
            source="hex_data",
        )

    elif "pattern" in args:
        pattern = args["pattern"]
        buf = bytearray(input_size)

        if pattern == 0:  # Silence — zeros
            pass  # already zeros
        elif pattern == 1:  # Noise
            for i in range(input_size):
                buf[i] = (i * 37 + 13) & 0xFF
        elif pattern == 2:  # Yes-like
            features = 40
            for t in range(10, 35):
                for f in range(5, 25):
                    idx = t * features + f
                    if idx < input_size:
                        buf[idx] = 80 & 0xFF
        elif pattern == 3:  # No-like
            features = 40
            for t in range(15, 40):
                for f in range(10, 30):
                    idx = t * features + f
                    if idx < input_size:
                        buf[idx] = 60 & 0xFF
        else:
            return _err("invalid_pattern", f"Pattern must be 0-3, got {pattern}")

        await backend.mem_write(buf_ptr, bytes(buf))
        pattern_names = {0: "silence", 1: "noise", 2: "yes-like", 3: "no-like"}
        write_result = _ok(
            written=input_size,
            buffer_address=f"0x{buf_ptr:08X}",
            source=f"pattern_{pattern}",
            pattern_name=pattern_names[pattern],
        )

    else:
        return _err("no_data", "Provide 'pattern' (0-3) or 'data_hex'")

    # Auto-trigger inference unless disabled
    if args.get("run_inference", True):
        _status, err = await _trigger_inference(session, backend)
        if err:
            write_result["inference_error"] = err
            return write_result

        # Read output and merge into result
        output = await handle_read_output(state, args)
        write_result.update(
            {
                "predicted_class": output.get("predicted_class"),
                "confidence": output.get("confidence"),
                "categories": output.get("categories"),
            }
        )

    return write_result


async def handle_read_input(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    buf_sym = _lookup(session, "tflm_input_buffer")
    size_sym = _lookup(session, "tflm_input_size")

    if not buf_sym or not size_sym:
        return _err("no_elf", "Required symbols not found — attach ELF first")

    buf_ptr = await _read_u32(backend, buf_sym[0])
    if buf_ptr == 0:
        return _err("not_initialized", "Input buffer is null — run micro_speech_init() first")

    input_size = await _read_i32(backend, size_sym[0])

    # Read the full input buffer
    raw = await backend.mem_read(buf_ptr, input_size)
    signed = [struct.unpack("<b", bytes([b]))[0] for b in raw]

    non_zero = sum(1 for v in signed if v != 0)
    min_val = min(signed)
    max_val = max(signed)
    mean_val = sum(signed) / len(signed)

    # First frame (40 features) as preview
    frame_size = min(40, input_size)
    first_frame = signed[:frame_size]

    return _ok(
        buffer_address=f"0x{buf_ptr:08X}",
        input_size=input_size,
        min=min_val,
        max=max_val,
        mean=round(mean_val, 2),
        non_zero_count=non_zero,
        non_zero_pct=f"{100 * non_zero / input_size:.1f}",
        first_frame=first_frame,
    )


async def _trigger_preprocess(session, backend):
    """Set tflm_run_preprocess=1, resume, wait for completion."""
    import asyncio

    trigger_sym = _lookup(session, "tflm_run_preprocess")
    if not trigger_sym:
        return None, "tflm_run_preprocess symbol not found — rebuild firmware"

    await backend.mem_write(trigger_sym[0], struct.pack("<i", 1))
    await backend.go()

    # Preprocessing (~50ms) + inference (~31ms) ≈ 80ms, poll at 100ms intervals
    for _ in range(30):  # up to 3 seconds
        await asyncio.sleep(0.1)
        val = await _read_i32(backend, trigger_sym[0])
        if val == 0:
            break

    await backend.halt()
    return "ok", None


async def handle_write_pcm(state: ProbeState, args: dict) -> dict:
    """Write a .wav file as raw PCM to the device and run preprocess+infer."""
    import os
    import wave

    session = state.get_session(args["session_id"])
    backend = session.backend

    fpath = args.get("file", "")
    if not fpath or not os.path.isfile(fpath):
        return _err("file_not_found", f"File not found: {fpath}")

    # Read and validate .wav file
    try:
        with wave.open(fpath, "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            raw_bytes = wf.readframes(nframes)
    except Exception as e:
        return _err("wav_error", f"Failed to read wav: {e}")

    if nchannels != 1:
        return _err("wav_format", f"Expected mono, got {nchannels} channels")
    if sampwidth != 2:
        return _err("wav_format", f"Expected 16-bit, got {sampwidth * 8}-bit")
    if framerate != 16000:
        return _err("wav_format", f"Expected 16kHz, got {framerate}Hz")

    # Convert to 16000 samples (1 second), zero-pad or truncate
    TARGET_SAMPLES = 16000
    TARGET_BYTES = TARGET_SAMPLES * 2
    if len(raw_bytes) >= TARGET_BYTES:
        pcm_data = raw_bytes[:TARGET_BYTES]
    else:
        pcm_data = raw_bytes + b"\x00" * (TARGET_BYTES - len(raw_bytes))

    # Find PCM buffer symbol
    pcm_sym = _lookup(session, "tflm_pcm_buffer")
    if not pcm_sym:
        return _err("no_symbol", "tflm_pcm_buffer symbol not found — rebuild firmware")

    # Write PCM data to device
    await backend.mem_write(pcm_sym[0], pcm_data)

    # Trigger preprocess + inference
    _status, err = await _trigger_preprocess(session, backend)
    if err:
        return _err("trigger_failed", err)

    # Read inference output
    output = await handle_read_output(state, args)

    return _ok(
        source=os.path.basename(fpath),
        wav_samples=nframes,
        wav_duration_ms=int(nframes * 1000 / framerate),
        pcm_bytes_written=TARGET_BYTES,
        predicted_class=output.get("predicted_class"),
        confidence=output.get("confidence"),
        categories=output.get("categories"),
    )


async def handle_accuracy_test(state: ProbeState, args: dict) -> dict:
    """Run wav files from labeled directories and compute accuracy."""
    import glob
    import os
    import random
    import wave

    session = state.get_session(args["session_id"])
    backend = session.backend

    test_dir = args.get("directory", "")
    if not test_dir or not os.path.isdir(test_dir):
        return _err("not_found", f"Directory not found: {test_dir}")

    max_per_class = int(args.get("samples_per_class", 50))
    seed = int(args.get("seed", 42))
    random.seed(seed)

    # Which classes to test (optional filter)
    classes_arg = args.get("classes", "")
    test_classes = [c.strip() for c in classes_arg.split(",")] if classes_arg else None

    # Model labels — configurable via 'labels' parameter
    model_labels = _parse_labels(args)
    label_set = set(model_labels)

    # Collect (path, expected_label) pairs.
    # Subdirectory names are matched directly against the label list.
    # Directories whose name is not in the label list are skipped.
    samples = []
    for subdir in sorted(os.listdir(test_dir)):
        subpath = os.path.join(test_dir, subdir)
        if not os.path.isdir(subpath):
            continue

        # Map directory name to label — exact match against label list
        if subdir in label_set:
            label = subdir
        elif subdir == "_background_noise_" and "silence" in label_set:
            label = "silence"
        else:
            continue

        # Skip classes not requested
        if test_classes and label not in test_classes:
            continue

        wavs = glob.glob(os.path.join(subpath, "*.wav"))
        if not wavs:
            continue

        picked = random.sample(wavs, min(max_per_class, len(wavs)))
        samples.extend((p, label) for p in picked)

    if not samples:
        return _err("no_samples", "No wav files found in subdirectories")

    random.shuffle(samples)

    # Resolve symbols once
    pcm_sym = _lookup(session, "tflm_pcm_buffer")
    if not pcm_sym:
        return _err("no_symbol", "tflm_pcm_buffer not found")

    TARGET_BYTES = 16000 * 2

    # Run each sample
    results = []
    correct = 0
    total = 0
    errors = 0
    confusion = {e: {p: 0 for p in model_labels} for e in model_labels}

    # Resolve output symbols once
    buf_sym = _lookup(session, "tflm_output_buffer")
    out_size_sym = _lookup(session, "tflm_output_size")
    scale_sym = _lookup(session, "tflm_output_scale")
    zp_sym = _lookup(session, "tflm_output_zero_point")
    if not all([buf_sym, out_size_sym, scale_sym, zp_sym]):
        return _err("no_elf", "Output symbols not found — attach ELF first")

    buf_ptr = await _read_u32(backend, buf_sym[0])
    out_size = await _read_i32(backend, out_size_sym[0])
    scale = await _read_f32(backend, scale_sym[0])
    zp = await _read_i32(backend, zp_sym[0])

    for _i, (fpath, expected) in enumerate(samples):
        try:
            with wave.open(fpath, "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                    errors += 1
                    continue
                raw = wf.readframes(wf.getnframes())
        except Exception:
            errors += 1
            continue

        if len(raw) >= TARGET_BYTES:
            pcm_data = raw[:TARGET_BYTES]
        else:
            pcm_data = raw + b"\x00" * (TARGET_BYTES - len(raw))

        try:
            # Write PCM — same as write_pcm handler (no halt needed,
            # J-Link supports memory writes while target is running)
            await backend.mem_write(pcm_sym[0], pcm_data)
            _status, err = await _trigger_preprocess(session, backend)
            if err:
                errors += 1
                continue

            raw_bytes = await backend.mem_read(buf_ptr, out_size)
        except Exception:
            # Connection likely died — return partial results
            break

        raw_vals = [struct.unpack("<b", bytes([b]))[0] for b in raw_bytes]
        scores = [(v - zp) * scale for v in raw_vals]
        best_idx = scores.index(max(scores))
        predicted = model_labels[best_idx] if best_idx < len(model_labels) else f"class_{best_idx}"
        confidence = max(scores)

        is_correct = predicted == expected
        if is_correct:
            correct += 1
        total += 1
        confusion[expected][predicted] += 1

        results.append(
            {
                "file": os.path.basename(fpath),
                "expected": expected,
                "predicted": predicted,
                "confidence": round(confidence, 4),
                "correct": is_correct,
            }
        )

    # Per-class accuracy
    per_class = {}
    for label in model_labels:
        row = confusion.get(label, {})
        class_total = sum(row.values())
        if class_total > 0:
            class_correct = row.get(label, 0)
            per_class[label] = {
                "total": class_total,
                "correct": class_correct,
                "accuracy_pct": round(100 * class_correct / class_total, 1),
            }

    # Misclassified samples (for debugging)
    wrong = [r for r in results if not r["correct"]]

    return _ok(
        total=total,
        correct=correct,
        accuracy_pct=round(100 * correct / total, 1) if total > 0 else 0,
        errors=errors,
        per_class=per_class,
        confusion_matrix=confusion,
        misclassified=wrong[:50],  # cap at 50 to keep response size reasonable
        seed=seed,
    )


TOOLS.append(
    Tool(
        name="tflite_micro.accuracy_test",
        description=(
            "Run a batch accuracy test. Takes a directory containing labeled "
            "subdirectories of .wav files. Each subdirectory name must match "
            "a label from the 'labels' parameter (e.g. yes/, no/, silence/). "
            "Randomly samples N files per class, runs on-device preprocessing "
            "+ inference for each, and returns accuracy stats with confusion matrix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "directory": {
                    "type": "string",
                    "description": "Path to directory with labeled subdirectories of .wav files",
                },
                "samples_per_class": {
                    "type": "integer",
                    "description": "Max samples per class (default 50)",
                    "default": 50,
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducibility (default 42)",
                    "default": 42,
                },
                "classes": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of classes to test (e.g. 'yes,no'). "
                        "Default: all classes from 'labels'."
                    ),
                },
                "labels": {
                    "type": "string",
                    "description": (
                        "Comma-separated class labels matching output tensor order "
                        "(default: 'silence,unknown,yes,no'). Subdirectory names in "
                        "the test directory are mapped to these labels."
                    ),
                },
            },
            "required": ["session_id", "directory"],
        },
    )
)


HANDLERS = {
    "tflite_micro.model_info": handle_model_info,
    "tflite_micro.arena_info": handle_arena_info,
    "tflite_micro.tensor_info": handle_tensor_info,
    "tflite_micro.read_output": handle_read_output,
    "tflite_micro.write_input": handle_write_input,
    "tflite_micro.read_input": handle_read_input,
    "tflite_micro.infer": handle_infer,
    "tflite_micro.write_pcm": handle_write_pcm,
    "tflite_micro.accuracy_test": handle_accuracy_test,
}
