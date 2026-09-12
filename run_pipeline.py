#!/usr/bin/env python3
"""The speech-to-speech pipeline, with the voice we asked for.

Same arguments as the `speech-to-speech` command; bridge.py starts it through here instead.

Kokoro's handler chooses its language -- and with it, its voice -- from the language the
*microphone* heard, not the one it is speaking. A sermon preached in English hands it "en",
so it loads the British phonemiser, and the Chinese translation comes back out as the
English words "Chinese letter", once per character, because English G2P has no phonemes for
神. --kokoro_lang_code cannot prevent it: the handler overwrites it on the first utterance.
So empty the table it looks the spoken language up in. Unknown language means "keep what you
have", and what it has is the voice bridge.py passed on the command line.

# ponytail: patching a dict in someone else's module. If upstream lets the target language
# reach the TTS handler -- a --kokoro_follow_output flag, say -- delete this file and go back
# to calling `speech-to-speech` directly from bridge.py.
"""
import sys

from speech_to_speech.TTS import kokoro_handler
from speech_to_speech.cli import main

kokoro_handler.WHISPER_LANGUAGE_TO_KOKORO_LANG.clear()

if __name__ == "__main__":
    sys.exit(main())
