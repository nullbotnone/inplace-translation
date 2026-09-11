#!/bin/bash
# Starts the translation pipeline and the broadcast, and stops both together.
# Run it by hand, or let launchd run it (see com.church.sermon-translate.plist).
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

MODEL=${MODEL:-mlx-community/Qwen3-4B-Instruct-2507-4bit}
STT=${STT:-mlx-audio-whisper}
TTS=${TTS:-qwen3}
AUDIO_DEVICE=${AUDIO_DEVICE:-}          # input name from: python3 -m sounddevice

# Kill the whole process group on exit, so a dead bridge never leaves models resident.
trap 'kill 0' EXIT INT TERM

caffeinate -i speech-to-speech serve \
    --mac-optimal-settings \
    --stt "$STT" --language auto \
    --tts "$TTS" \
    --model_name "$MODEL" \
    --chat_size 2 --num_pipelines 1 &

echo "waiting for the pipeline to load models (first run downloads ~8 GB)..."
for _ in $(seq 1 600); do
    nc -z 127.0.0.1 8765 && break
    sleep 1
done
nc -z 127.0.0.1 8765 || { echo "pipeline never came up on :8765"; exit 1; }

# No exec: the trap above has to survive so a dying bridge takes the models down with it.
if [ -n "$AUDIO_DEVICE" ]; then
    caffeinate -i python3 bridge.py --device "$AUDIO_DEVICE"
else
    caffeinate -i python3 bridge.py
fi
