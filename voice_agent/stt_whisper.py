"""Faster-Whisper STT plugin for livekit-agents 1.x."""
from __future__ import annotations

import asyncio
import logging
import os

import numpy as np
from faster_whisper import WhisperModel
from livekit.agents import stt, utils, APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

logger = logging.getLogger(__name__)

WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")
WHISPER_LANG = os.environ.get("WHISPER_LANG", "ru")

_model: WhisperModel | None = None


def _get_model(model_size: str) -> WhisperModel:
    global _model
    if _model is None:
        logger.info("Loading Whisper model '%s'...", model_size)
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        logger.info("Whisper model loaded.")
    return _model


class WhisperSTT(stt.STT):
    def __init__(self, model_size: str = WHISPER_MODEL_SIZE, language: str = WHISPER_LANG):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model_size = model_size
        self._language = language

    async def _recognize_impl(
        self,
        buffer: stt.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang = language if language is not NOT_GIVEN else self._language

        merged = utils.merge_frames(buffer)
        pcm = np.frombuffer(merged.data, dtype=np.int16).astype(np.float32) / 32768.0

        loop = asyncio.get_event_loop()
        model = await loop.run_in_executor(None, lambda: _get_model(self._model_size))

        def _transcribe():
            segments, _ = model.transcribe(
                pcm,
                language=lang,
                beam_size=5,
                vad_filter=True,
            )
            return " ".join(s.text.strip() for s in segments).strip()

        text = await loop.run_in_executor(None, _transcribe)
        logger.info("STT result: %r", text)

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(text=text, language=lang, confidence=1.0)],
        )
