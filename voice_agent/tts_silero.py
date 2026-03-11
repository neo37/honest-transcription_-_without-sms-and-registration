"""Silero TTS plugin for livekit-agents 1.x."""
from __future__ import annotations

import asyncio
import logging
import os

import numpy as np
import torch
from livekit import rtc
from livekit.agents import tts, utils, APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
NUM_CHANNELS = 1
_model = None
_model_lock = asyncio.Lock()


async def _get_model():
    global _model
    async with _model_lock:
        if _model is None:
            logger.info("Loading Silero TTS model...")
            loop = asyncio.get_event_loop()
            model, _ = await loop.run_in_executor(
                None,
                lambda: torch.hub.load(
                    "snakers4/silero-models",
                    "silero_tts",
                    language="ru",
                    speaker="v3_1_ru",
                    trust_repo=True,
                ),
            )
            model.to(torch.device("cpu"))
            _model = model
            logger.info("Silero TTS model loaded.")
    return _model


class SileroTTS(tts.TTS):
    def __init__(self, speaker: str = "xenia"):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._speaker = speaker

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "SileroStream":
        return SileroStream(tts=self, input_text=text, conn_options=conn_options)


class SileroStream(tts.ChunkedStream):
    def __init__(self, *, tts: SileroTTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._speaker = tts._speaker

    async def _run(self, output_emitter) -> None:
        model = await _get_model()

        def _synth():
            audio = model.apply_tts(
                text=self._input_text,
                speaker=self._speaker,
                sample_rate=SAMPLE_RATE,
            )
            pcm = (audio * 32767).clamp(-32768, 32767).short().numpy()
            return pcm

        pcm = await asyncio.get_event_loop().run_in_executor(None, _synth)

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/pcm",
            stream=False,
        )

        output_emitter.push(pcm.tobytes())
        output_emitter.flush()
