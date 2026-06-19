# stt_models/

faster-whisper (Whisper) models for **voice input** (speech-to-text) live here.

The model named in `models.toml` `[stt].model` (default `base`) is **auto-downloaded
once** from Hugging Face on first use, then runs fully local / offline — like an
`ollama pull` or the Piper `.onnx`, not a runtime API call.

- Install the engine: `pip install -e ".[web,audio]"`.
- Sizes: `tiny`/`base` (fast, CPU) · `small`/`medium` (better French, a bit slower).
- CPU + `int8` by default (no VRAM contention with Ollama) ; switch to `cuda`/`float16` in `[stt]`.

Everything here is gitignored except this README.
