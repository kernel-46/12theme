"""Telegram notifier — fires on auto-handover so the human officer is
pinged on their phone with full case context.

Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env to enable.
If either is missing, all calls become silent no-ops.

Free, no rate limits. Works from anywhere — Telegram's API is global.
"""
from __future__ import annotations
import os
import httpx
from typing import Dict, Any, Optional

_API = "https://api.telegram.org/bot{token}/sendMessage"


def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "")


# Backwards-compat names (some old call sites read these as module attrs).
def __getattr__(name):
    if name == "BOT_TOKEN":
        return _bot_token()
    if name == "CHAT_ID":
        return _chat_id()
    raise AttributeError(name)


def is_enabled() -> bool:
    return bool(_bot_token() and _chat_id())


def _format_handover(call_id: str, info: Dict[str, Any]) -> str:
    lines = [
        "🚨 <b>Pratyaya · HUMAN HANDOVER</b>",
        f"<b>Call ID:</b> <code>{call_id}</code>",
    ]
    if info.get("language"):  lines.append(f"<b>Language:</b> {info['language']}")
    if info.get("dialect"):   lines.append(f"<b>Dialect:</b> {info['dialect']}")
    if info.get("issue_type"):lines.append(f"<b>Issue:</b> {info['issue_type']}")
    if info.get("urgency"):   lines.append(f"<b>Urgency:</b> {info['urgency']}")
    if info.get("location"):  lines.append(f"<b>Location:</b> {info['location']}")
    if info.get("distress") is not None:
        lines.append(f"<b>Distress:</b> {info['distress']:.2f}")
    if info.get("summary"):
        lines.append("")
        lines.append(f"<b>Summary:</b>\n{info['summary']}")
    if info.get("transcript"):
        snippet = info["transcript"][:280]
        lines.append("")
        lines.append(f"<b>Citizen said:</b>\n<i>{snippet}</i>")
    if info.get("reasons"):
        lines.append("")
        lines.append(f"<b>Why escalated:</b> {', '.join(info['reasons'])}")
    if info.get("dashboard_url"):
        lines.append("")
        lines.append(f"➡ <a href=\"{info['dashboard_url']}\">Open call in agent dashboard</a>")
    return "\n".join(lines)


async def notify_handover(call_id: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Best-effort send to Telegram. Never raises — failures are logged only."""
    token = _bot_token()
    chat = _chat_id()
    if not (token and chat):
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(_API.format(token=token), data={
                "chat_id": chat,
                "text": _format_handover(call_id, info),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            })
            return {"status": r.status_code, "ok": r.is_success}
    except Exception as e:
        return {"status": 0, "ok": False, "error": str(e)[:120]}
