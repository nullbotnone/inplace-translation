"""Piper as a voice for the pipeline, registered by run_pipeline.py.

Piper runs on the CPU, which is the whole reason it is here. Kokoro is an MLX model, so it
takes the same GPU lock the translator holds, and a sentence cannot be spoken until the
translator has finished writing the next one -- that is what the voice buffer exists to hide.
Measured on this Mac, one sentence of English:

    GPU idle   Piper 0.049 s   Kokoro 0.077 s
    GPU busy   Piper 0.051 s   Kokoro 0.553 s

Idle they are the same speed. Busy is what a sermon actually is.

A voice is one ~60 MB onnx file plus its config, downloaded on first use into
~/.cache/piper-voices, and it speaks one language: the name says which.
"""

from __future__ import annotations

import logging
from math import gcd
from pathlib import Path
from threading import Event
from typing import Any, Iterator, Optional

import numpy as np

from speech_to_speech.baseHandler import BaseHandler
from speech_to_speech.pipeline.cancel_scope import CancelScope
from speech_to_speech.pipeline.handler_types import TTSIn, TTSOut
from speech_to_speech.pipeline.messages import AUDIO_RESPONSE_DONE, EndOfResponse
from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

logger = logging.getLogger(__name__)

VOICES_DIR = Path.home() / ".cache/piper-voices"
RATE = 16000                      # what the pipeline passes around; Piper's voices are 22050


class PiperTTSHandler(BaseHandler[TTSIn, TTSOut]):
    """Text to speech with Piper, on the CPU, off the GPU lock."""

    def setup(
        self,
        should_listen: Event,
        voice: str = "en_US-ryan-medium",
        speed: float = 1.0,
        blocksize: int = 512,
        voices_dir: Optional[str] = None,
        gen_kwargs: dict[str, Any] | None = None,
        cancel_scope: CancelScope | None = None,
        speculative_turns: SpeculativeTurnTracker | None = None,
    ) -> None:
        from piper import PiperVoice, SynthesisConfig
        from piper.download_voices import download_voice

        self.should_listen = should_listen
        self.blocksize = blocksize
        self.cancel_scope = cancel_scope
        self.speculative_turns = speculative_turns
        # length_scale is duration, so it runs the other way round from speed. Left at None
        # the voice keeps whatever its own config asks for.
        self.syn_config = SynthesisConfig(length_scale=None if speed == 1.0 else 1.0 / speed)

        directory = Path(voices_dir) if voices_dir else VOICES_DIR
        directory.mkdir(parents=True, exist_ok=True)
        if not (directory / f"{voice}.onnx").exists():
            logger.info("Downloading Piper voice %s into %s", voice, directory)
            download_voice(voice, directory)

        self.voice = PiperVoice.load(directory / f"{voice}.onnx", download_dir=directory)
        # 22050 -> 16000 is 320/441. Ask gcd rather than hard-coding it: the x_low voices are
        # already at 16000, where this becomes the no-op it should be.
        source_rate = self.voice.config.sample_rate
        divisor = gcd(source_rate, RATE)
        self.resample = (RATE // divisor, source_rate // divisor)
        logger.info("Loaded Piper voice %s at %d Hz", voice, source_rate)
        self.warmup()

    def warmup(self) -> None:
        """Through _speak, not the model: the first sentence otherwise pays for the scipy
        import and the first inference both -- 0.4 s against 0.06 s for every one after it --
        and the first sentence is the preacher's opening line."""
        for _ in self._speak("Hello"):
            pass

    def process(self, tts_input: TTSIn) -> Iterator[TTSOut]:
        speculative_turns = getattr(self, "speculative_turns", None)
        if isinstance(tts_input, EndOfResponse):
            if speculative_turns and not speculative_turns.is_latest_after_reopen_grace(
                tts_input.turn_id, tts_input.turn_revision
            ):
                if tts_input.response_key is None:
                    return
                tts_input.cleanup_only = True
            yield AUDIO_RESPONSE_DONE
            return

        if speculative_turns and not speculative_turns.is_latest_after_reopen_grace(
            tts_input.turn_id, tts_input.turn_revision
        ):
            logger.debug("Dropping stale TTS input for turn=%s rev=%s",
                         tts_input.turn_id, tts_input.turn_revision)
            return
        if speculative_turns:
            speculative_turns.commit(tts_input.turn_id, tts_input.turn_revision)

        # The voice is a loaded model, one language each, chosen in the console before the
        # pipeline started. Nothing mid-sermon can change it, so language_code is not read.
        yield from self._speak(tts_input.text)

    def _speak(self, sentence: str) -> Iterator[np.ndarray]:
        from scipy.signal import resample_poly

        generation = self.cancel_scope.generation if self.cancel_scope else None
        for chunk in self.voice.synthesize(sentence, self.syn_config):
            audio = resample_poly(chunk.audio_float_array, *self.resample)
            audio = (audio * 32768).astype(np.int16)
            for start in range(0, len(audio), self.blocksize):
                if generation is not None and self.cancel_scope is not None \
                        and self.cancel_scope.is_stale(generation):
                    logger.info("TTS generation cancelled (interruption)")
                    return
                block = audio[start:start + self.blocksize]
                if len(block) < self.blocksize:
                    block = np.pad(block, (0, self.blocksize - len(block)))
                yield block
