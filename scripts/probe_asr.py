"""Diagnostic — sends a real audio blob to Groq via our own asr.py path,
prints the FULL raw response (including any error). Run while the backend
is up: `python scripts/probe_asr.py path/to/audio.webm`.

If you don't have an audio file handy, this script also generates a 2-second
synthetic speech-like buzz and sends that — Whisper should at least respond
with `text: ""` (no error) on synthetic audio, which proves the pipe is open.
"""
import asyncio
import sys
import json
import math
import struct
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services import groq_client  # noqa: E402
from backend import config  # noqa: E402


def _synthetic_wav(duration_s: float = 2.0, sr: int = 16000) -> bytes:
    """A tiny PCM-WAV blob (a 200 Hz tone). Whisper accepts WAV cleanly."""
    n = int(duration_s * sr)
    samples = bytearray()
    for i in range(n):
        v = int(0.3 * 32767 * math.sin(2 * math.pi * 200 * i / sr))
        samples += struct.pack("<h", v)
    data = bytes(samples)
    # WAV header (PCM, mono, 16-bit)
    header = b"RIFF"
    header += struct.pack("<I", 36 + len(data))
    header += b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def _pp(label, obj):
    """UTF-8 safe printer — Windows cp1252 console can't print Kannada
    directly, so we write the full JSON to a file and print a short
    ASCII-only summary to the console.
    """
    out_dir = ROOT / "data" / "probe_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = label.replace(" ", "_").replace("=", "").replace(",", "")[:80]
    p = out_dir / f"{safe}.json"
    try:
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        p.write_text(f"<dump error: {e}>", encoding="utf-8")
    text = (obj.get("text") or "").strip() if isinstance(obj, dict) else ""
    err  = obj.get("error") if isinstance(obj, dict) else None
    lang = obj.get("language") if isinstance(obj, dict) else None
    print(f"  -> file={p.name}  text_len={len(text)}  language={lang!r}  error={err!r}")


async def main():
    print(f"[probe] GROQ_API_KEY={'set' if config.GROQ_API_KEY else 'MISSING'} "
          f"len={len(config.GROQ_API_KEY)}")
    print(f"[probe] ASR_MODEL={config.ASR_MODEL}")

    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        audio = path.read_bytes()
        fname = path.name
        print(f"[probe] sending real file {path} ({len(audio)} bytes)")
    else:
        audio = _synthetic_wav(2.0)
        fname = "synthetic.wav"
        print(f"[probe] sending synthetic 2s tone WAV ({len(audio)} bytes)")

    # 1) With our short prompt
    print("\n[probe] === Call 1: language=auto, no prompt ===")
    out = await groq_client.transcribe(audio, fname, None, prompt=None)
    _pp("call1_auto_noprompt", out)

    # 2) With explicit kn
    print("\n[probe] === Call 2: language=kn, no prompt ===")
    out = await groq_client.transcribe(audio, fname, "kn", prompt=None)
    _pp("call2_kn_noprompt", out)

    # 3) With explicit en
    print("\n[probe] === Call 3: language=en, no prompt ===")
    out = await groq_client.transcribe(audio, fname, "en", prompt=None)
    _pp("call3_en_noprompt", out)

    # 4) With short helpline prompt (matches asr.py path)
    print("\n[probe] === Call 4: language=kn, short helpline prompt ===")
    short_prompt = "Karnataka 1092 women and child helpline call in Kannada, Hindi, or English."
    out = await groq_client.transcribe(audio, fname, "kn", prompt=short_prompt)
    _pp("call4_kn_shortprompt", out)


if __name__ == "__main__":
    asyncio.run(main())
