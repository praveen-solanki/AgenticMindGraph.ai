"""
utils/multi_llm_client.py
=========================
Multi-provider LLM client for the ASEI agent layer.

All inference now routes through NVIDIA NIM (integrate.api.nvidia.com/v1).
Each task type has a primary NVIDIA model and one or more NVIDIA fallbacks.
The local vLLM endpoint is still used for the "local_reasoning" debate leg
to keep that traffic off external APIs.

Usage
-----
    from utils.multi_llm_client import call_agent_llm, call_agent_llm_json

    text   = call_agent_llm("heavy_reasoning", system_prompt, user_prompt)
    result = call_agent_llm_json("synthesis", system_prompt, user_prompt)

Provider chain per task type (all NVIDIA NIM unless noted):
    heavy_reasoning  → Prosecutor (qwen3.5-397b) → Defender (deepseek-v4-pro) → Skeptic (llama-3.3-70b)
    synthesis        → Synthesis (qwen3.5-122b-a10b) → SynthFB (nemotron-mini-4b)
    mid_reasoning    → Defender (deepseek-v4-pro) → Skeptic (llama-3.3-70b)
    fast_classify    → ClassifyFB (gemma-3n-e4b) → Skeptic (llama-3.3-70b)
    summarization    → Summarization (mistral-medium-3.5-128b) → Impact (qwen3-next-80b-instruct)
    gap_detection    → GapPrimary (gemma-3-12b) → GapFallback (phi-4-mini)
    impact           → Impact (qwen3-next-80b-instruct) → ImpactFB (mixtral-8x22b)
    local_reasoning  → local vLLM Qwen2.5-72B (debate skeptic leg)
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import httpx

from config import settings
from utils.logger import get_logger

log = get_logger("multi_llm_client")


# ── Rate-limit defences ───────────────────────────────────────────────────────

import urllib.parse as _urlparse

# How long (seconds) a provider HOST is skipped after receiving a 429.
# Overridden upward by the Retry-After header when present.
COOLDOWN_SECONDS: int = 60

# Max concurrent in-flight requests to any single provider HOST.
MAX_CONCURRENT_PER_HOST: int = 1  # NVIDIA NIM handles concurrency better than free-tier providers

# host → earliest monotonic time when that API account may be used again.
_cooldown_until: dict[str, float] = {}
_cooldown_lock = threading.Lock()

# host → threading.Semaphore (created lazily)
_semaphores: dict[str, threading.Semaphore] = {}
_sem_lock = threading.Lock()


def _host_key(provider: dict) -> str:
    """
    Rate-limit / cooldown key = host + model name.
    This ensures a cooldown on one NVIDIA model does NOT block other models
    on the same host — each model has its own independent cooldown bucket.
    """
    parsed = _urlparse.urlparse(provider["base_url"])
    host   = parsed.netloc or provider["base_url"]
    return f"{host}::{provider['model']}"


def _is_cooling(provider: dict) -> bool:
    """Return True if this provider's host is still in its cooldown window."""
    with _cooldown_lock:
        until = _cooldown_until.get(_host_key(provider), 0.0)
    return time.monotonic() < until


def _set_cooldown(provider: dict, retry_after: float | None = None) -> None:
    """Mark a host unavailable for COOLDOWN_SECONDS (or retry_after if longer)."""
    wait = max(COOLDOWN_SECONDS, retry_after or 0.0)
    key  = _host_key(provider)
    with _cooldown_lock:
        _cooldown_until[key] = time.monotonic() + wait
    log.info("  Cooldown set: host=%s for %.0f s ", key, wait)


def _get_semaphore(provider: dict) -> threading.Semaphore:
    """Return the threading.Semaphore for this provider host, creating if needed."""
    key = _host_key(provider)
    with _sem_lock:
        if key not in _semaphores:
            _semaphores[key] = threading.Semaphore(MAX_CONCURRENT_PER_HOST)
        return _semaphores[key]


# ── Provider configs ──────────────────────────────────────────────────────────

def _provider(base_url: str, api_key: str, model: str, extra_headers: dict | None = None, is_local: bool = False, extra_payload: dict | None = None) -> dict:
    return {
        "base_url":      base_url,
        "api_key":       api_key,
        "model":         model,
        "extra_headers": extra_headers or {},
        "is_local":      is_local,
        "extra_payload": extra_payload or {},
    }

# Task → ordered list of providers to try (first = primary, rest = fallbacks)
TASK_PROVIDER_CHAINS: dict[str, list[dict]] = {}

# Per-model request rate limiter (token bucket, thread-safe)
# Prevents hammering NVIDIA NIM free tier (5 RPM per model default)
_rate_tokens: dict[str, float] = {}   # model → available tokens (float)
_rate_last:   dict[str, float] = {}   # model → last refill monotonic time
_rate_lock  = threading.Lock()

def _rate_limit_wait(model: str) -> None:
    """
    Block until a request token is available for this model.
    Refills at settings.NVIDIA_RPM_LIMIT tokens per 60 seconds.
    Only applied to NVIDIA models (not local vLLM).
    """
    rpm      = settings.NVIDIA_RPM_LIMIT
    interval = 60.0 / rpm   # seconds between allowed requests

    with _rate_lock:
        now = time.monotonic()
        if model not in _rate_tokens:
            _rate_tokens[model] = rpm          # start full
            _rate_last[model]   = now

        # Refill tokens based on elapsed time
        elapsed             = now - _rate_last[model]
        _rate_tokens[model] = min(rpm, _rate_tokens[model] + elapsed * (rpm / 60.0))
        _rate_last[model]   = now

        if _rate_tokens[model] >= 1.0:
            _rate_tokens[model] -= 1.0
            wait = 0.0
        else:
            wait = (1.0 - _rate_tokens[model]) * interval
            _rate_tokens[model] = 0.0

    if wait > 0:
        log.debug("  Rate throttle: waiting %.1f s before calling %s", wait, model)
        time.sleep(wait)

def _build_chains() -> None:
    """
    Build provider chains from settings.
    All external inference uses NVIDIA NIM.
    Local vLLM is retained only for the 'local_reasoning' debate leg.
    """
    global TASK_PROVIDER_CHAINS

    # ── NVIDIA NIM helpers ────────────────────────────────────────────────────
    def nvidia(model: str, **extra_payload) -> dict:
        return _provider(settings.NVIDIA_BASE_URL, settings.NVIDIA_API_KEY, model, extra_payload=extra_payload)

    # Then each model definition becomes:
    prosecutor  = nvidia(settings.NVIDIA_MODEL_PROSECUTOR,    # qwen3.5-397b
                        temperature=0.60, top_p=0.95, top_k=20,
                        # presence_penalty=0, repetition_penalty=1,
                        chat_template_kwargs={"enable_thinking": False})

    defender    = nvidia(settings.NVIDIA_MODEL_DEFENDER,       # deepseek-v4-pro
                        temperature=1.0, top_p=0.95,
                        chat_template_kwargs={"thinking": False})

    skeptic     = nvidia(settings.NVIDIA_MODEL_SKEPTIC,        # llama-3.3-70b
                        temperature=0.2, top_p=0.7)

    synthesis   = nvidia(settings.NVIDIA_MODEL_SYNTHESIS,      # qwen/qwen3.5-122b-a10b
                        temperature=0.60, top_p=0.95,
                        chat_template_kwargs={"enable_thinking": False})

    synth_fb    = nvidia(settings.NVIDIA_MODEL_SYNTH_FB,       # nemotron-mini-4b
                        temperature=0.2, top_p=0.7)

    conflict    = nvidia(settings.NVIDIA_MODEL_CONFLICT,        # llama-4-maverick
                        temperature=1.0, top_p=1.0,
                        frequency_penalty=0, presence_penalty=0)

    verification= nvidia(settings.NVIDIA_MODEL_VERIFICATION,   # llama-3.1-70b
                        temperature=0.2, top_p=0.7)

    gap_super   = nvidia(settings.NVIDIA_MODEL_GAP_SUPER,    # nvidia/nemotron-3-super-120b-a12b
                        temperature=1.0, top_p=0.95,
                        chat_template_kwargs={"enable_thinking": False},
                        reasoning_budget=16384)

    gap_primary = nvidia(settings.NVIDIA_MODEL_GAP_PRIMARY,    # mistralai/ministral-14b-instruct-2512
                        temperature=0.15, top_p=1.0)

    gap_fb      = nvidia(settings.NVIDIA_MODEL_GAP_FALLBACK,   # phi-4-mini
                        temperature=0.1, top_p=0.7)

    summarize   = nvidia(settings.NVIDIA_MODEL_SUMMARIZATION,  # mistralai/mistral-medium-3.5
                        temperature=0.7, top_p=1.0,
                        reasoning_effort="high")


    impact      = nvidia(settings.NVIDIA_MODEL_IMPACT,         # qwen3-next-80b-instruct
                        temperature=0.6, top_p=0.7)

    impact_fb   = nvidia(settings.NVIDIA_MODEL_IMPACT_FB,      # mixtral-8x22b
                        temperature=0.5, top_p=1.0)

    classify_fb = nvidia(settings.NVIDIA_MODEL_CLASSIFY_FB,    # gemma-3n-e4b
                        temperature=0.2, top_p=0.7,
                        frequency_penalty=0, presence_penalty=0)

    # ── Local vLLM (debate skeptic leg only) ──────────────────────────────────
    # local = _provider(settings.VLLM_BASE_URL, settings.VLLM_API_KEY, settings.LLM_MODEL)
    local = _provider(settings.VLLM_BASE_URL, settings.VLLM_API_KEY, settings.LLM_MODEL, is_local=True)


    TASK_PROVIDER_CHAINS = {
        # Ordered from most capable -> least capable
        # local always last as guaranteed fallback

        "heavy_reasoning": [
            prosecutor,   # qwen3.5-397b-a17b
            # skeptic,      # llama-3.3-70b-instruct
            local,
            # defender,     # deepseek-v4-pro
        ],

        "synthesis": [
            defender,     # deepseek-v4-pro
            impact_fb,    # mixtral-8x22b
            # synthesis,    # qwen/qwen3.5-122b-a10b
            local,
        ],

        "mid_reasoning": [
            synthesis,    # qwen/qwen3.5-122b-a10b
            # verification, # llama-3.1-70b-instruct
            local
        ],

        "fast_classify": [
            skeptic,      # llama-3.3-70b-instruct
            classify_fb,  # gemma-3n-e4b-it
            local
        ],

        "summarization": [
            summarize,    # mistral-medium-3.5-128b
            # impact,       # qwen3-next-80b-a3b-instruct
            local
        ],

        "gap_detection": [
            gap_super,    # "nvidia/nemotron-3-super-120b-a12b"
            # skeptic,      # llama-3.3-70b-instruct
            gap_primary,  # gemma-3-12b-it
            gap_fb,       # phi-4-mini-instruct
            local
        ],

        "impact": [
            impact,       # qwen3-next-80b-a3b-instruct
            # impact_fb,    # mixtral-8x22b-instruct-v0.1
            skeptic,      # llama-3.3-70b-instruct
            local
        ],

        "local_reasoning": [
            local
        ],
    }

    # TASK_PROVIDER_CHAINS = {
    #     # LOCAL FIRST — no rate limits, no timeouts, no cooldowns
    #     "heavy_reasoning":  [local, skeptic, defender, prosecutor],
    #     "synthesis":        [local, synth_fb, synthesis, defender],
    #     "mid_reasoning":    [local, skeptic, verification, defender],
    #     "fast_classify":    [local, classify_fb, skeptic],
    #     "summarization":    [local, summarize, impact],
    #     "gap_detection":    [local, gap_primary, gap_fb, skeptic],
    #     "impact":           [local, impact, impact_fb, skeptic],
    #     "local_reasoning":  [local],
    # }

_build_chains()


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def call_agent_llm(
        task_type: str,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        retries: int = 2,
    ) -> str:
    """
    Call LLM for a given task type, trying providers in fallback order.
    Returns raw text response.  Returns "" on total failure.

    Args:
        task_type:   Key into TASK_PROVIDER_CHAINS (e.g. "heavy_reasoning").
        system:      System prompt.
        user:        User prompt.
        max_tokens:  Max output tokens.
        temperature: Sampling temperature (0 = deterministic).
        retries:     Per-provider retry count on transient errors.
    """
    chain = TASK_PROVIDER_CHAINS.get(task_type)
    if not chain:
        log.warning("Unknown task_type '%s', falling back to heavy_reasoning", task_type)
        chain = TASK_PROVIDER_CHAINS["heavy_reasoning"]

    last_error: str = ""
    for provider in chain:
        if not provider["api_key"] and not provider["extra_headers"]:
            log.debug("Skipping provider %s — no API key configured", provider["base_url"])
            continue

        # Skip providers that are still cooling down from a recent 429
        if _is_cooling(provider):
            log.debug("  Skipping %s — still in cooldown", provider["model"])
            log.info("  Skipping %s — still in cooldown", provider["model"])
            continue

        sem      = _get_semaphore(provider)
        is_local = provider.get("is_local", False)
        provider_retries  = settings.NVIDIA_RETRIES if not is_local else 2

        for attempt in range(provider_retries  + 1):
            try:
                # Apply per-model rate throttle for NVIDIA providers only
                if not is_local:
                    _rate_limit_wait(provider["model"])

                with sem:
                    text = _call_provider(provider, system, user, max_tokens, temperature)
                if text:
                    return text
            except RateLimitError as exc:
                retry_after = exc.retry_after
                log.info(
                    "  Rate limited on %s (%s), cooldown %.0f s, moving to fallback",
                    provider["model"], provider["base_url"],
                    max(COOLDOWN_SECONDS, retry_after or 0),
                )
                _set_cooldown(provider, retry_after=COOLDOWN_SECONDS * 2)
                break  # don't retry this provider
            # except Exception as exc:
            #     last_error = str(exc)
            #     err_lower  = last_error.lower()
            #     # Treat timeouts as provider unavailable — cooldown and move on immediately
            #     if "timed out" in err_lower or "time out" in err_lower or "timeout" in err_lower:
            #         log.warning(
            #             "  Timeout on %s — setting cooldown, skipping to fallback",
            #             provider["model"],
            #         )
            #         _set_cooldown(provider, retry_after=120)
            #         break  # don't retry on timeout
            #     if attempt < provider_retries:
            #         time.sleep(1.5 ** attempt)
            #     else:
            #         log.warning(
            #             "  Provider %s failed after %d attempts: %s",
            #             provider["model"], provider_retries  + 1, exc,
            #         )

            except Exception as exc:
                last_error = str(exc)
                err_lower  = last_error.lower()

                log.warning(
                    "  Exception on %s — type=%s message=%s",
                    provider["model"], type(exc).__name__, str(exc),
                )

                # Config/code bug — don't treat as provider failure or set cooldown
                if isinstance(exc, AttributeError):
                    log.error(
                        "  Configuration error — fix required, not setting cooldown: %s", exc
                    )
                    break

                if "timed out" in err_lower or "time out" in err_lower or "timeout" in err_lower:
                    is_connect_timeout = "connect" in err_lower or "pool" in err_lower
                    cooldown = 30 if is_connect_timeout else 120
                    log.warning(
                        "  %s on %s — setting cooldown %ds, skipping to fallback",
                        "Connect timeout" if is_connect_timeout else "Read timeout",
                        provider["model"], cooldown,
                    )
                    _set_cooldown(provider, retry_after=cooldown)
                    break

                if attempt < provider_retries:
                    time.sleep(1.5 ** attempt)
                else:
                    log.warning(
                        "  Provider %s failed after %d attempts: %s",
                        provider["model"], provider_retries + 1, exc,
                    )

    log.error("All providers failed for task '%s'. Last error: %s", task_type, last_error)
    return ""


def call_agent_llm_json(
        task_type: str,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        retries: int = 2,
    ) -> dict | list | None:
    """
    Same as call_agent_llm but parses the response as JSON.
    Returns None on parse failure or total provider failure.
    """
    raw = call_agent_llm(task_type, system, user, max_tokens, temperature, retries)
    return _parse_json(raw) if raw else None


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

class RateLimitError(Exception):
    """Raised when a provider returns HTTP 429."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after  # seconds to wait, from Retry-After header


def _call_provider(
        provider: dict,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
    """
    Make a single HTTP call to an OpenAI-compatible endpoint.
    Raises RateLimitError on HTTP 429, generic Exception on other errors.
    """
    base_url = provider["base_url"].rstrip("/")
    api_key  = provider["api_key"]
    model    = provider["model"]
    extra_h  = provider.get("extra_headers", {})
    is_local = provider.get("is_local", False)

    # Bosch endpoint has the full path baked in — don't append /chat/completions
    if "chat/completions" in base_url:
        url = base_url
    else:
        url = f"{base_url}/chat/completions"

    headers: dict = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(extra_h)

    payload = {
        "model":    model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "max_tokens": max_tokens if is_local
                      else settings.NVIDIA_MAX_TOKENS_BY_MODEL.get(model, settings.NVIDIA_MAX_TOKENS),
        "temperature": temperature,
    }

    # For NVIDIA models, overlay the per-model recommended params
    # (top_p, top_k, penalties, reasoning_effort, chat_template_kwargs, etc.)
    # These override the generic defaults set above, including temperature.
    if not is_local:
        payload.update(provider.get("extra_payload", {}))

    # Per-model timeout for NVIDIA, global LLM_TIMEOUT for local
    timeout = (
        httpx.Timeout(settings.LLM_TIMEOUT, connect=10.0)
        if is_local else
        httpx.Timeout(
            settings.NVIDIA_TIMEOUT_BY_MODEL.get(model, settings.NVIDIA_TIMEOUT_S),
            connect=10.0,
        )
    )

    with httpx.Client(verify=False, timeout=timeout) as client:
        log.info("Calling model: %s @ %s", model, base_url)
        resp = client.post(url, headers=headers, json=payload)

    if resp.status_code == 429:
        retry_after: float | None = None
        raw_ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if raw_ra:
            try:
                retry_after = float(raw_ra)
            except ValueError:
                pass
        raise RateLimitError(f"429 from {model} @ {base_url}", retry_after=retry_after)

    if resp.status_code != 200:
        raise Exception(f"HTTP {resp.status_code} from {model}: {resp.text[:500]}")

    data    = resp.json()
    choice  = data["choices"][0]
    content = choice["message"]["content"]

    # Warn if the model was cut off by the token limit — invisible otherwise
    finish_reason = choice.get("finish_reason", "")
    if finish_reason == "length":
        log.warning(
            "  Output truncated (finish_reason=length) on %s — "
            "max_tokens=%d may be too low for this response",
            model, payload["max_tokens"],
        )

    return content.strip() if content else ""


def _parse_json(raw: str) -> dict | list | None:
    """Strip markdown fences and parse JSON. Returns None on failure."""
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    log.debug("JSON parse failed: %.120s", raw)
    return None