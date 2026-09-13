#!/usr/bin/env python3
"""The speech-to-speech pipeline, with the voice we asked for and the two languages we serve.

Same arguments as the `speech-to-speech` command; bridge.py starts it through here instead.

Kokoro's handler chooses its language -- and with it, its voice -- from the language the
*microphone* heard, not the one it is speaking. A sermon preached in English hands it "en",
so it loads the British phonemiser, and the Chinese translation comes back out as the
English words "Chinese letter", once per character, because English G2P has no phonemes for
神. --kokoro_lang_code cannot prevent it: the handler overwrites it on the first utterance.
So empty the table it looks the spoken language up in. Unknown language means "keep what you
have", and what it has is the voice bridge.py passed on the command line.

Second patch, for "Detect automatically": Whisper picks the spoken language from all 99 it
knows, and Mandarin over a room mic lands on Japanese often enough to matter -- a whole
utterance then gets decoded as kana and the translation is nonsense. This service only ever
translates between English and Chinese, so the detector only ever gets to answer "en" or
"zh": the most likely of those two, whatever the other 97 scored. Nothing to restart or
reconfigure when it is wrong about Japanese -- Japanese is not on the ballot.

Third patch, for what Whisper hears in a room that is not talking: handed a cough, an organ
chord or the PA's hum, it does not return nothing -- it loops one word for the length of the
clip ("wires, wires, wires, wires, ..."), and that gets translated and spoken over the
sermon. Whisper's own tell for this is how well the text compresses, and the pipeline throws
the transcription away before the translator ever sees it.

# ponytail: patching dicts and a method in someone else's modules. If upstream lets the
# target language reach the TTS handler (a --kokoro_follow_output flag, say) and takes a
# candidate list for detection (--languages en,zh), delete this file and go back to calling
# `speech-to-speech` directly from bridge.py.
"""
import sys, zlib

from speech_to_speech.STT.base_stt_handler import BaseSTTHandler
from speech_to_speech.TTS import kokoro_handler
from speech_to_speech.cli import main
from mlx_audio.stt.models.whisper import whisper as mlx_whisper

kokoro_handler.WHISPER_LANGUAGE_TO_KOKORO_LANG.clear()

# The handler receives the *input* language with every TTS chunk. On an English output from a
# Chinese sermon that is "zh", which is a fact about the microphone and not about the text now
# being synthesised. It also accepts an OpenAI-style response voice, which is unrelated to the
# Kokoro voice selected in the console. Both can replace the configured American phonemiser and
# voice after startup. Keep the loaded target-language pair authoritative at the final point
# before MLX generates audio.
_process_mlx = kokoro_handler.KokoroTTSHandler._process_mlx


def _process_mlx_in_configured_language(self, text, language_code=None):
    voice, lang = self._initial_voice, self._initial_lang_code
    if self.voice != voice or self.lang_code != lang:
        self.voice, self.lang_code = voice, lang
        self._pipeline = self.model._get_pipeline(lang)
        self._voice_tensor = self._pipeline.load_voice(voice)
    yield from _process_mlx(self, text, language_code=None)


kokoro_handler.KokoroTTSHandler._process_mlx = _process_mlx_in_configured_language

# The only two languages this service recognises, translates, or speaks. bridge.py's SPOKEN
# and TARGETS are the same two: a language listeners cannot be sent to is no use heard.
LANGUAGES = ("en", "zh")


def pick(probs):
    """The likeliest of our two languages. A code we never saw scores 0, so en wins a
    detection that was sure about something else entirely -- and en is Whisper's own
    fallback for audio it cannot place."""
    return max(LANGUAGES, key=lambda code: probs.get(code, 0.0))


def _detect_language(self, mel, language=None):
    """Replaces Model._detect_language, which is argmax over every language Whisper knows.

    Same shape as the original, including the two early returns: a language named on the
    command line is authoritative, and an English-only model has nothing to detect.
    """
    if language is not None:
        return language
    if not self.is_multilingual:
        return "en"
    mel_segment = mlx_whisper.pad_or_trim(
        mel, mlx_whisper.N_FRAMES, axis=-2).astype(self.dtype)
    _, probs = self.detect_language(mel_segment)
    return pick(probs)


mlx_whisper.Model._detect_language = _detect_language

# How much better than its own bytes a transcription has to compress before it is a loop
# rather than a sentence. Whisper's own number, used inside its decoder for the same
# judgement. Measured on sermon lines: English prose 1.1, a Chinese verse 1.1, "Amen, amen,
# amen" 1.0 -- and 5.4 for the wires, 7.8 for a 谢谢观看 loop. Nothing real comes close.
LOOP_RATIO = 2.4


def looping(text):
    """True for the one word Whisper repeats to fill a clip that had no speech in it."""
    raw = text.strip().encode()
    return bool(raw) and len(raw) / len(zlib.compress(raw)) >= LOOP_RATIO


_should_emit_output = BaseSTTHandler.should_emit_output


def should_emit_output(self, output):
    """Drop a looping transcription where the pipeline already drops stale ones.

    On the base class, so it covers whichever recogniser is configured. Dropping it here is
    what keeps it out of the translator, off the subtitles and out of the voice; the turn is
    still marked finished, or the next partial for it would be recognised all over again.
    """
    if looping(getattr(output, "text", "")):
        print(f"!! dropped a looping transcription: {output.text.strip()[:60]!r}", flush=True)
        self.before_emit_output(output)
        return False
    return _should_emit_output(self, output)


BaseSTTHandler.should_emit_output = should_emit_output

if __name__ == "__main__":
    sys.exit(main())
