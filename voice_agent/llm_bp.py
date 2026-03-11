"""Business Pad LLM (r-ai) plugin for livekit-agents 1.x."""
from __future__ import annotations

import logging
import os
import uuid

import aiohttp
from livekit.agents import llm, APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger(__name__)

BP_LLM_URL      = os.environ.get("LLM_URL",          "https://r-ai.business-pad.com/api/ai_request/")
BP_LLM_AUTH     = os.environ.get("LLM_AUTH",         "")
BP_LLM_REFERER  = os.environ.get("LLM_REFERER",      "https://core.business-pad.com/")
BP_LLM_RESP_KEY = os.environ.get("LLM_RESPONSE_KEY", "messages")
BP_LLM_MODEL    = os.environ.get("LLM_MODEL",        "gpt-4.1-mini")
BP_LLM_SESSION  = os.environ.get("LLM_SESSION_ID",   "thread_newsagent")
BP_LLM_USER     = os.environ.get("LLM_USER",         "openai")


class BPLLMStream(llm.LLMStream):
    def __init__(self, llm_inst: "BPLLM", chat_ctx: llm.ChatContext, conn_options: APIConnectOptions):
        super().__init__(llm_inst, chat_ctx=chat_ctx, tools=[], conn_options=conn_options)
        self._llm = llm_inst

    async def _run(self) -> None:
        parts = []
        for msg in self._chat_ctx.items:
            if not hasattr(msg, "role"):
                continue
            content = ""
            if isinstance(msg.content, str):
                content = msg.content
            elif isinstance(msg.content, list):
                content = " ".join(
                    c.text if hasattr(c, "text") else str(c) for c in msg.content
                )
            label = {"system": "[Система]", "user": "[Пользователь]", "assistant": "[Ассистент]"}.get(msg.role, msg.role)
            parts.append(f"{label}: {content}")

        prompt = "\n".join(parts)
        request_id = str(uuid.uuid4())

        body = {
            "question_to_send": prompt,
            "session_id": BP_LLM_SESSION,
            "user": BP_LLM_USER,
            "log_id": "log",
            "model": BP_LLM_MODEL,
        }

        headers = {
            "Authorization": self._llm._auth,
            "Referer": self._llm._referer,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._llm._url,
                    headers=headers,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            raw = data.get(self._llm._response_key) or data.get("response", "")
            text = raw[-1] if isinstance(raw, list) and raw else str(raw)
            logger.info("LLM: %s", text[:200])

            self._event_ch.send_nowait(
                llm.ChatChunk(
                    id=request_id,
                    delta=llm.ChoiceDelta(role="assistant", content=text),
                )
            )
        except Exception as e:
            logger.exception("LLM error: %s", e)


async def _call_llm(prompt: str) -> str:
    """Прямой вызов LLM без AgentSession — для задач и анализа."""
    body = {
        "question_to_send": prompt,
        "session_id": BP_LLM_SESSION,
        "user": BP_LLM_USER,
        "log_id": "log",
        "model": BP_LLM_MODEL,
    }
    headers = {
        "Authorization": BP_LLM_AUTH,
        "Referer": BP_LLM_REFERER,
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                BP_LLM_URL,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        raw = data.get(BP_LLM_RESP_KEY) or data.get("response", "")
        return raw[-1] if isinstance(raw, list) and raw else str(raw)
    except Exception as e:
        logger.exception("_call_llm error: %s", e)
        return ""


class BPLLM(llm.LLM):
    def __init__(self):
        super().__init__()
        self._url          = BP_LLM_URL
        self._auth         = BP_LLM_AUTH
        self._referer      = BP_LLM_REFERER
        self._response_key = BP_LLM_RESP_KEY

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **kwargs,
    ) -> BPLLMStream:
        return BPLLMStream(self, chat_ctx, conn_options)
