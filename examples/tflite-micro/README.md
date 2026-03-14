# tflite_micro — TFLite Micro Debug Probe Plugin

Inspect and interact with a TFLite Micro model running on-device through the J-Link debug probe. Inject audio, trigger inference, and read predictions — all without modifying firmware.

## Requirements

- A debug probe session (`dbgprobe.connect`)
- ELF file attached (`dbgprobe.elf.attach`)
- Firmware must export these global symbols:

| Symbol | Type | Purpose |
|--------|------|---------|
| `tensor_arena` | `uint8_t[]` | Tensor arena buffer |
| `<model_symbol>` | `uint8_t[]` | TFLite FlatBuffer in flash (configurable via `model_symbol` param, default: `g_micro_speech_quantized_model_data`) |
| `tflm_input_buffer` | `int8_t*` | Resolved input tensor pointer |
| `tflm_output_buffer` | `int8_t*` | Resolved output tensor pointer |
| `tflm_input_size` | `int32_t` | Input element count |
| `tflm_output_size` | `int32_t` | Output element count |
| `tflm_output_scale` | `float` | Output quantization scale |
| `tflm_output_zero_point` | `int32_t` | Output quantization zero point |
| `tflm_run_inference` | `volatile int32_t` | Write 1 to trigger inference |
| `tflm_run_preprocess` | `volatile int32_t` | Write 1 to trigger preprocess + inference |
| `tflm_pcm_buffer` | `int16_t[16000]` | Raw PCM input for on-device preprocessing |
| `tflm_arena_used` | `int32_t` | Exact arena usage from `arena_used_bytes()` |

## Tools

### Inspection

| Tool | Description |
|------|-------------|
| `tflite_micro.model_info` | Read model metadata from flash — size, FlatBuffer ID, schema version. Optional `model_symbol` to specify the ELF symbol name |
| `tflite_micro.arena_info` | Read tensor arena address, total/used size, utilization percentage |
| `tflite_micro.tensor_info` | Read input/output buffer addresses, sizes, quantization params |

### Data injection and inference

| Tool | Description |
|------|-------------|
| `tflite_micro.write_input` | Write features to input tensor — from `.bin` file, hex string, or test pattern (0=silence, 1=noise, 2=yes-like, 3=no-like). Auto-triggers inference by default. Optional `labels` |
| `tflite_micro.write_pcm` | Write a `.wav` file (mono, 16kHz, 16-bit) as raw PCM, run on-device preprocessing + inference. The main end-to-end tool. Optional `labels` |
| `tflite_micro.infer` | Trigger inference on current input tensor contents. Optional `labels` |

### Reading results

| Tool | Description |
|------|-------------|
| `tflite_micro.read_output` | Read output scores — raw int8 values + dequantized floats with predicted class label. Optional `labels` |
| `tflite_micro.read_input` | Read input tensor summary — min/max/mean/non-zero count + first MFCC frame |

### Batch testing

| Tool | Description |
|------|-------------|
| `tflite_micro.accuracy_test` | Run batch accuracy test over a directory of labeled .wav files. Returns per-class accuracy, confusion matrix, and misclassified samples. Optional `labels` |

## Configurable labels

Most tools accept an optional `labels` parameter — a comma-separated string matching the output tensor order. This maps raw output indices to human-readable class names.

```
# Default (micro_speech model)
labels="silence,unknown,yes,no"

# Custom model with different classes
labels="cat,dog,bird,fish"
```

If omitted, defaults to `silence,unknown,yes,no`. The `accuracy_test` tool uses labels to match subdirectory names to output classes — each subdirectory name must appear in the labels list.

## How it works

The plugin resolves firmware global symbols via ELF lookup, then reads/writes target RAM through the debug probe backend. No firmware halt or interruption needed for reads.

For inference triggering (`infer`, `write_input`, `write_pcm`):
1. Write data to the appropriate buffer
2. Set the trigger flag (`tflm_run_inference=1` or `tflm_run_preprocess=1`)
3. Resume the target
4. Poll until the firmware clears the flag (flag is cleared *after* work completes)
5. Halt and read results

## Usage examples

```
# Connect and attach ELF
dbgprobe.connect(device="nRF52840_xxAA")
dbgprobe.elf.attach(path="build/peripheral_uart/zephyr/zephyr.elf")

# End-to-end: wav file → on-device preprocessing → inference → result
tflite_micro.write_pcm(file="/path/to/yes_1000ms.wav")
→ {"predicted_class": "yes", "confidence": 0.996, ...}

# Inject pre-computed features
tflite_micro.write_input(file="/path/to/features.bin")
→ {"predicted_class": "no", "confidence": 0.93, ...}

# Inject test pattern and infer
tflite_micro.write_input(pattern=0)  # silence
→ {"predicted_class": "silence", "confidence": 0.438, ...}

# Inspect model and memory
tflite_micro.model_info()
→ {"model_size": 18800, "flatbuffer_id": "TFL3", "region": "flash"}

tflite_micro.arena_info()
→ {"arena_size": 7168, "used": 6948, "utilization_pct": "96.9%", "source": "arena_used_bytes()"}

# Batch accuracy test (Google Speech Commands dataset)
tflite_micro.accuracy_test(directory="test_data", classes="yes,no", samples_per_class=50)
→ {"total": 65, "correct": 62, "accuracy_pct": 95.4,
   "per_class": {"yes": {"accuracy_pct": 96.9}, "no": {"accuracy_pct": 93.9}},
   "confusion_matrix": {...}, "misclassified": [...]}
```

## Output format

`read_output` and inference results include:

```json
{
  "predicted_class": "yes",
  "confidence": 0.996,
  "categories": {
    "silence": {"raw_int8": -128, "score": 0.0},
    "unknown": {"raw_int8": -128, "score": 0.0},
    "yes": {"raw_int8": 126, "score": 0.996},
    "no": {"raw_int8": -110, "score": 0.0703}
  }
}
```

## Example model (micro_speech)

The default configuration targets the micro_speech keyword-spotting model:

- **micro_speech** quantized — 18.8 KB, int8
- 4 classes: silence, unknown, yes, no
- Input: [1, 49, 40] — 49 MFCC time frames x 40 frequency bins
- Ops: DepthwiseConv2D, FullyConnected, Softmax, Reshape
- Kernels: CMSIS-NN (ARM Cortex-M4 optimized)

For other models, set `model_symbol` and `labels` to match your firmware.
