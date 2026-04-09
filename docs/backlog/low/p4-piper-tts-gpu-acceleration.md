# P4: PiperTTS GPU Acceleration

## Summary
Currently loads ONNX model with default CPU execution provider.

## Context
Configure CUDA EP on prod (RTX 4090) and CoreML EP on dev (M4 Max) for faster synthesis.

## Acceptance Criteria
- CUDA execution provider on Linux/prod
- CoreML execution provider on macOS/dev
- Auto-detection of available providers
