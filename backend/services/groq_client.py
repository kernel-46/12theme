"""Thin async wrapper over the Groq HTTP API.
We avoid the official SDK so we can run audio + chat from one shared httpx client.
"""
from __future__ import annotations
import httpx
import asyncio
import json
from typing import Optional, Dict, Any
from .. import config

GROQ_BASE = "https://api.groq.com/openai/v1"

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=GROQ_BASE,
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        )
    return _client


async def transcribe(audio_bytes: bytes, filename: str = "audio.webm",
                     language: Optional[str] = None,
                     prompt: Optional[str] = None) -> Dict[str, Any]:
    """Call Groq Whisper. Returns {text, language, segments?, words?}.
    Uses verbose_json so we can extract per-segment confidence proxies.

    The `prompt` parameter primes Whisper's vocabulary — strongly improves
    Kannada/Hindi recall on dialect-rich utterances and code-mixed speech.
    """
    client = get_client()
    # Pick a sensible content-type from filename suffix.
    suffix = (filename or "audio.webm").lower().rsplit(".", 1)[-1]
    ctype = {
        "webm": "audio/webm", "ogg": "audio/ogg", "mp3": "audio/mpeg",
        "wav": "audio/wav", "m4a": "audio/mp4", "mp4": "audio/mp4",
        "flac": "audio/flac",
    }.get(suffix, "audio/webm")

    files = {"file": (filename, audio_bytes, ctype)}
    data = {
        "model": config.ASR_MODEL,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    if language and language != "auto":
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    try:
        r = await client.post("/audio/transcriptions", files=files, data=data)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"asr_error: {e.response.text[:200]}", "text": ""}
    except Exception as e:
        return {"error": f"asr_exception: {e}", "text": ""}


async def chat_json(system: str, user: str,
                    model: Optional[str] = None,
                    temperature: float = 0.0,
                    max_tokens: int = 800) -> Dict[str, Any]:
    """Call Groq chat completions and parse a JSON response."""
    client = get_client()
    body = {
        "model": model or config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    try:
        r = await client.post("/chat/completions", json=body)
        r.raise_for_status()
        out = r.json()
        content = out["choices"][0]["message"]["content"]
        return json.loads(content)
    except httpx.HTTPStatusError as e:
        return {"_error": f"llm_http: {e.response.status_code} {e.response.text[:200]}"}
    except json.JSONDecodeError:
        return {"_error": "llm_parse_error", "_raw": content[:300] if 'content' in locals() else ""}
    except Exception as e:
        return {"_error": f"llm_exception: {e}"}


async def chat_text(system: str, user: str,
                    model: Optional[str] = None,
                    temperature: float = 0.2,
                    max_tokens: int = 400) -> str:
    """Call Groq chat completions for a free-text response."""
    client = get_client()
    body = {
        "model": model or config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        r = await client.post("/chat/completions", json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return ""
