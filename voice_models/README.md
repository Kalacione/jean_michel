# Voice models for Jean-Michel's vocal mode

This folder hosts Piper TTS voice models (`.onnx` + `.onnx.json`) used by
the `--mode vocal` of Jean-Michel. Models are NOT committed to git
(they're 30-150 MB each) ; only this README is tracked.

## System prerequisites (Linux)

Before vocal mode produces any sound, your user must be able to access
the audio hardware. On Manjaro / Arch / most desktop distros the audio
devices in `/dev/snd/*` are restricted to the `audio` group OR to the
user owning the current `seat0` (graphical login). When you're working
from a terminal, SSH, or a CLI session, you're typically NOT on seat0 —
so the group membership is the reliable path.

Check :

```bash
groups | tr ' ' '\n' | grep audio       # should print "audio"
aplay -l                                # should list your sound cards
pactl get-default-sink                  # should NOT print "auto_null"
```

If `audio` is missing :

```bash
sudo usermod -aG audio $USER
```

Then **close your session completely and log back in** (or reboot — that's
the no-surprise path). Group changes are only picked up at session login,
and the `systemd --user` daemon that runs PipeWire needs to restart with
the new groups for PipeWire to expose real sinks.

`./jm.sh --install` checks this automatically and warns if the group is
missing.

## How vocal mode picks the model

Set `JEANMICHEL_VOICE_MODEL` (env var or `.env`) to the absolute path of
the `.onnx` file you want to use. The matching `.onnx.json` config file
is auto-discovered by Piper (same path + `.json`), so you don't need to
configure it separately.

```bash
# In .env (loaded at startup) :
JEANMICHEL_VOICE_MODEL=/home/jeremy/projects/jean-michel/voice_models/fr_FR-glados-medium.onnx
```

If the env var is unset, the CLI falls back to looking for
`voice_models/default.onnx` at the repo root. If neither resolves, vocal
mode degrades gracefully : the text response is shown as usual, with a
warning that no voice was synthesised.

## Where to download voice models

Piper publishes a [model catalogue on Hugging Face](https://huggingface.co/rhasspy/piper-voices/tree/main).
Each model ships as a `.onnx` (the neural network) + `.onnx.json` (sample
rate, speakers, phoneme set) pair — download BOTH.

### Recommended French voices

| Voice                       | Quality | Sample rate | Size  | Notes                                |
|-----------------------------|---------|-------------|-------|--------------------------------------|
| `fr_FR-siwis-medium`        | medium  | 22050 Hz    | 63 MB | Default Piper French (clean, neutral)|
| `fr_FR-tom-medium`          | medium  | 22050 Hz    | 63 MB | Male, calm                           |
| `fr_FR-upmc-medium`         | medium  | 22050 Hz    | 63 MB | Female, broadcast-like               |
| `fr_FR-glados-medium`       | medium  | 22050 Hz    | 63 MB | Fine-tune from siwis, GLaDOS-style   |

### English

| Voice                          | Quality | Notes                       |
|--------------------------------|---------|-----------------------------|
| `en_US-amy-medium`             | medium  | Default English             |
| `en_US-libritts_r-medium`      | medium  | Multi-speaker (1 = default) |
| `en_GB-alan-medium`            | medium  | British male                |

## Manual download

```bash
cd voice_models/
# Pick the voice from the HF catalogue then :
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json
```

## Audio playback

The CLI tries these players in order (first available wins) :
`paplay` (PulseAudio), `aplay` (ALSA), `ffplay -nodisp -autoexit`
(FFmpeg). On Arch / Manjaro all three are typically present out of the
box ; on a server distro you may need to install one of them.

To force a specific player, set `JEANMICHEL_AUDIO_PLAYER` to its command
name (`paplay`, `aplay`, `ffplay`, or any command that accepts a WAV
file as the last argument).
