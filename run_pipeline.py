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

# ponytail: patching dicts and a method in someone else's modules. If upstream lets the
# target language reach the TTS handler (a --kokoro_follow_output flag, say) and takes a
# candidate list for detection (--languages en,zh), delete this file and go back to calling
# `speech-to-speech` directly from bridge.py.
"""
import sys

from speech_to_speech.TTS import kokoro_handler
from speech_to_speech.cli import main
from mlx_audio.stt.models.whisper import whisper as mlx_whisper

kokoro_handler.WHISPER_LANGUAGE_TO_KOKORO_LANG.clear()

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

if __name__ == "__main__":
    sys.exit(main())
