"""
utils/llm_client.py
====================
Shared synchronous and async LLM client for all pipeline stages.

All stages use the same ChatOpenAI instance configured from settings.
Provides:
  - get_llm()         → ChatOpenAI (LangChain)
  - call_llm_json()   → synchronous call, returns parsed JSON dict
  - call_llm_text()   → synchronous call, returns raw string
  - acall_llm_json()  → async call, returns parsed JSON dict
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
from langchain_openai import ChatOpenAI

from config import settings
from utils.logger import get_logger

log = get_logger("llm_client")

# ── Module-level singleton ────────────────────────────────────────────────────
_llm_instance: ChatOpenAI | None = None


def get_llm() -> ChatOpenAI:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.VLLM_API_KEY,
            base_url=settings.VLLM_BASE_URL,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT,
            http_client=httpx.Client(verify=False, timeout=settings.LLM_TIMEOUT),
        )
    return _llm_instance


def call_llm_text(system: str, user: str, retries: int = 2) -> str:
    """Synchronous LLM call. Returns raw text response."""
    from langchain_core.messages import SystemMessage, HumanMessage
    llm = get_llm()
    for attempt in range(retries + 1):
        try:
            response = llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=user),
            ])
            return response.content.strip()
        except Exception as exc:
            if attempt == retries:
                log.warning("LLM call failed after %d retries: %s", retries, exc)
                return ""
            log.debug("LLM retry %d: %s", attempt + 1, exc)
    return ""


def call_llm_json(system: str, user: str, retries: int = 2) -> dict | list | None:
    """
    Synchronous LLM call expecting JSON output.
    Strips markdown fences, returns parsed object or None on failure.
    """
    raw = call_llm_text(system, user, retries)
    return _parse_json(raw)


async def acall_llm_json(
    system: str,
    user: str,
    semaphore: asyncio.Semaphore | None = None,
    retries: int = 2,
) -> dict | list | None:
    """Async LLM call expecting JSON output."""
    from langchain_core.messages import SystemMessage, HumanMessage
    llm = get_llm()

    async def _try() -> dict | list | None:
        for attempt in range(retries + 1):
            try:
                response = await llm.ainvoke([
                    SystemMessage(content=system),
                    HumanMessage(content=user),
                ])
                return _parse_json(response.content.strip())
            except Exception as exc:
                if attempt == retries:
                    log.debug("Async LLM call failed: %s", exc)
                    return None
        return None

    if semaphore:
        async with semaphore:
            return await _try()
    return await _try()


async def acall_llm_text(
    system: str,
    user: str,
    semaphore: asyncio.Semaphore | None = None,
    retries: int = 2,
) -> str:
    """Async LLM call returning raw text."""
    from langchain_core.messages import SystemMessage, HumanMessage
    llm = get_llm()

    async def _try() -> str:
        for attempt in range(retries + 1):
            try:
                response = await llm.ainvoke([
                    SystemMessage(content=system),
                    HumanMessage(content=user),
                ])
                return response.content.strip()
            except Exception as exc:
                if attempt == retries:
                    log.debug("Async LLM text call failed: %s", exc)
                    return ""
        return ""

    if semaphore:
        async with semaphore:
            return await _try()
    return await _try()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict | list | None:
    """Strip markdown fences and parse JSON. Returns None on failure."""
    if not raw:
        return None
    # Strip ```json ... ``` or ``` ... ``` fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract first {...} or [...] block
        m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    log.debug("JSON parse failed on: %.120s", raw)
    return None