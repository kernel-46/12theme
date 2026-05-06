"""Generate architecture.svg — AWS-style deployment-flow diagram for Pratyaya.

Layout mirrors the reference style (numbered top pipeline, walled cloud
section in the middle, right-side service column, bottom request-flow
strip + key-points + sovereignty box).

Output: D:/theme12/architecture.svg  +  architecture.png (if cairosvg).
"""
from pathlib import Path
import textwrap


# ---------- palette (matches the v2 government CSS) ----------
JADE_DEEP   = "#064037"
JADE        = "#0d6b5e"
JADE_LIGHT  = "#2fa48a"
GOLD        = "#b07a18"
GOLD_LIGHT  = "#d9a44a"
CREAM       = "#faf6ed"
CREAM_2     = "#f3eddf"
CREAM_3     = "#ece4d2"
INK         = "#1f2a28"
INK_2       = "#36433f"
MUTED       = "#6b6a5e"
LINE        = "#b8ac90"
RED         = "#9f1239"
RED_LIGHT   = "#d44a6e"
GREEN       = "#166534"
GREEN_LIGHT = "#4a9b6c"
PLUM        = "#6b3982"
PLUM_LIGHT  = "#a87bb1"
WHITE       = "#ffffff"


W, H = 1600, 1040


def rect(x, y, w, h, *, fill=WHITE, stroke=None, sw=1.2, rx=10, dash=None, opacity=1.0):
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" fill="{fill}"'
    if stroke:
        s += f' stroke="{stroke}" stroke-width="{sw}"'
        if dash:
            s += f' stroke-dasharray="{dash}"'
    if opacity < 1.0:
        s += f' opacity="{opacity}"'
    s += "/>"
    return s


def text(x, y, txt, *, size=12, color=INK, weight="normal", anchor="start",
         family="Segoe UI, Calibri, Inter, sans-serif"):
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}" '
            f'dominant-baseline="middle">{txt}</text>')


def arrow(x1, y1, x2, y2, *, color=JADE_DEEP, sw=2.2, head_id="arrow-jade"):
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{sw}" '
            f'marker-end="url(#{head_id})"/>')


def numbered_circle(cx, cy, n, *, r=14, fill=JADE_DEEP, color=GOLD_LIGHT):
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" '
            f'stroke="{GOLD}" stroke-width="2"/>'
            f'<text x="{cx}" y="{cy}" font-family="Calibri" font-size="14" '
            f'font-weight="bold" fill="{color}" text-anchor="middle" '
            f'dominant-baseline="central">{n}</text>')


def lock_icon(x, y, *, color=JADE):
    return (f'<g transform="translate({x},{y})">'
            f'<rect x="0" y="6" width="14" height="11" rx="2" fill="{color}"/>'
            f'<path d="M3 6 V3 a4 4 0 0 1 8 0 V6" fill="none" '
            f'stroke="{color}" stroke-width="2"/></g>')


def emoji(x, y, ch, *, size=20):
    return (f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="middle" '
            f'dominant-baseline="middle">{ch}</text>')


def card(x, y, w, h, *, fill=WHITE, stroke=LINE, accent=None, accent_pos="left", radius=10):
    """A bordered card with an optional left/top accent stripe."""
    parts = [rect(x, y, w, h, fill=fill, stroke=stroke, sw=1.5, rx=radius)]
    if accent:
        if accent_pos == "left":
            parts.append(rect(x, y, 6, h, fill=accent, rx=0))
        elif accent_pos == "top":
            parts.append(rect(x, y, w, 6, fill=accent, rx=0))
    return "\n".join(parts)


def service_box(x, y, w, h, *, title, sub, color, fg=WHITE, icon="", icon_color=None):
    """A coloured service tile — header bar style. Returns string SVG."""
    g = []
    g.append(rect(x, y, w, h, fill=color, rx=8))
    if icon:
        g.append(emoji(x + 24, y + 24, icon, size=22))
    g.append(text(x + (44 if icon else 12), y + 22, title,
                  size=13, color=fg, weight="bold"))
    if sub:
        for i, line in enumerate(sub.split("\n")):
            g.append(text(x + 12, y + 48 + i * 16, line, size=10.5, color=fg))
    return "\n".join(g)


def build_svg():
    parts = []

    # ===== defs: arrow markers + drop shadow =====
    parts.append("""<defs>
  <marker id="arrow-jade" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#064037"/>
  </marker>
  <marker id="arrow-gold" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#b07a18"/>
  </marker>
  <marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#9f1239"/>
  </marker>
  <marker id="arrow-plum" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#6b3982"/>
  </marker>
  <filter id="soft" x="-10%" y="-10%" width="120%" height="120%">
    <feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.10"/>
  </filter>
</defs>""")

    # ===== background =====
    parts.append(rect(0, 0, W, H, fill=CREAM_2))

    # ====================================================
    # Banner
    # ====================================================
    parts.append(rect(0, 0, W, 56, fill=JADE_DEEP, rx=0))
    parts.append(rect(0, 56, W, 4, fill=GOLD, rx=0))
    parts.append(text(28, 28, "PRATYAYA · Architecture Diagram",
                      size=18, color=CREAM, weight="bold"))
    parts.append(text(28, 46, "AI for the 1092 Helpline   ·   Team Arjuna   ·   AI for Bharat 2 · Theme 12",
                      size=11, color=GOLD_LIGHT))
    parts.append(text(W - 28, 28, "Voice-to-voice  ·  Verified-understanding-first",
                      size=12, color=CREAM, weight="bold", anchor="end"))
    parts.append(text(W - 28, 46, "Open-source  ·  Indian-cloud  ·  RTI-ready",
                      size=10, color=GOLD_LIGHT, anchor="end"))

    # ====================================================
    # 1. TOP CONVERSATION PIPELINE  (mirrors CI/CD bar)
    # ====================================================
    pipe_y = 84; pipe_h = 200
    parts.append(rect(20, pipe_y, W - 40, pipe_h, fill=WHITE, stroke=PLUM, sw=2,
                      rx=14, dash="6 4"))
    parts.append(rect(580, pipe_y - 14, 480, 28, fill=PLUM, rx=14))
    parts.append(text(820, pipe_y, "CONVERSATION PIPELINE  ·  one turn",
                      size=14, color=CREAM, weight="bold", anchor="middle"))

    # 6 stages
    stages = [
        ("Citizen", "📞", "speaks naturally\nKn / Hi / En", JADE_DEEP),
        ("Browser",  "🌐", "MediaRecorder + VAD\nWebAudio analyser", JADE),
        ("Edge",     "🛡", "PII redaction\n(spaCy + Indian NER)", JADE_LIGHT),
        ("ASR",      "🎙", "Whisper-Large-v3\nscript-locked retry", GOLD),
        ("LLM",      "🤖", "Llama-3.3-70B\nfull chat history", GOLD_LIGHT),
        ("TTS",      "🔊", "edge-tts dialect voice\n→ back to citizen", PLUM),
    ]
    sx0 = 60; sy = pipe_y + 38; sw = 200; sh = 130; sgap = (W - 80 - sw * len(stages)) / (len(stages) - 1)
    for i, (label, ic, body, color) in enumerate(stages):
        x = sx0 + i * (sw + sgap)
        parts.append(rect(x, sy, sw, sh, fill=WHITE, stroke=color, sw=1.6, rx=10))
        parts.append(rect(x, sy, sw, 8, fill=color, rx=0))
        parts.append(emoji(x + 30, sy + 38, ic, size=22))
        parts.append(text(x + 60, sy + 38, label, size=14, color=color, weight="bold"))
        for j, line in enumerate(body.split("\n")):
            parts.append(text(x + 14, sy + 70 + j * 18, line, size=10.5, color=INK_2))
        # numbered circle on the arrow (between this and next box)
        if i < len(stages) - 1:
            ax1 = x + sw
            ax2 = x + sw + sgap
            mid = (ax1 + ax2) / 2
            parts.append(arrow(ax1 + 4, sy + sh / 2, ax2 - 4, sy + sh / 2,
                                color=JADE_DEEP, sw=2.5))
            parts.append(numbered_circle(mid, sy + sh / 2, i + 1))

    # ====================================================
    # 2. INDIAN CLOUD (main container)
    # ====================================================
    cloud_y = 304
    cloud_h = 540
    cloud_w = 1130
    cloud_x = 20
    parts.append(rect(cloud_x, cloud_y, cloud_w, cloud_h, fill=WHITE,
                      stroke=JADE, sw=2, rx=14))
    parts.append(rect(cloud_x, cloud_y, 200, 30, fill=JADE, rx=0))
    parts.append(text(cloud_x + 12, cloud_y + 15,
                      "🇮🇳  INDIAN CLOUD  ·  MeghRaj-ready",
                      size=12, color=CREAM, weight="bold"))

    # ----- VPC inside the cloud -----
    vpc_x = cloud_x + 22
    vpc_y = cloud_y + 50
    vpc_w = cloud_w - 44
    vpc_h = cloud_h - 70
    parts.append(rect(vpc_x, vpc_y, vpc_w, vpc_h, fill="#f7faf8",
                      stroke=JADE_LIGHT, sw=1.5, rx=10, dash="6 4"))
    parts.append(rect(vpc_x + 12, vpc_y - 12, 250, 24, fill=JADE_LIGHT, rx=12))
    parts.append(text(vpc_x + 24, vpc_y, "VPC · 10.0.0.0/16  ·  K8s namespace",
                      size=11, color=CREAM, weight="bold"))

    # Internet gateway (top centre)
    ig_cx = vpc_x + vpc_w / 2
    ig_cy = vpc_y + 38
    parts.append(f'<circle cx="{ig_cx}" cy="{ig_cy}" r="22" fill="{GOLD}" '
                 f'stroke="{GOLD_LIGHT}" stroke-width="3"/>')
    parts.append(emoji(ig_cx, ig_cy + 1, "🌐", size=18))
    parts.append(text(ig_cx + 38, ig_cy - 6, "Internet Gateway",
                      size=11, color=INK, weight="bold"))
    parts.append(text(ig_cx + 38, ig_cy + 9, "TLS · 443  ·  WSS",
                      size=10, color=MUTED))

    # ----- public subnet (left) — citizen-facing edge -----
    pub_x = vpc_x + 30; pub_y = vpc_y + 80
    pub_w = (vpc_w - 90) / 2; pub_h = 200
    parts.append(rect(pub_x, pub_y, pub_w, pub_h, fill=WHITE,
                      stroke=GREEN_LIGHT, sw=1.5, rx=10, dash="5 3"))
    parts.append(lock_icon(pub_x + 12, pub_y + 8, color=GREEN))
    parts.append(text(pub_x + 36, pub_y + 18,
                      "Public Subnet · 10.0.1.0/24  ·  citizen edge",
                      size=11, color=GREEN, weight="bold"))

    # FastAPI gateway box
    fa_x = pub_x + 24; fa_y = pub_y + 38; fa_w = pub_w - 48; fa_h = 70
    parts.append(rect(fa_x, fa_y, fa_w, fa_h, fill=JADE, rx=8))
    parts.append(emoji(fa_x + 24, fa_y + fa_h / 2, "⚡", size=22))
    parts.append(text(fa_x + 50, fa_y + 24, "FastAPI · Uvicorn",
                      size=13, color=CREAM, weight="bold"))
    parts.append(text(fa_x + 50, fa_y + 44, "WebSocket fan-out  ·  /converse · /converse_text",
                      size=10, color=CREAM))
    parts.append(text(fa_x + 50, fa_y + 60, "WSS  ·  asyncio · httpx",
                      size=10, color=GOLD_LIGHT))

    # PII edge worker
    pii_x = pub_x + 24; pii_y = fa_y + fa_h + 10; pii_w = (fa_w - 12) / 2; pii_h = 60
    parts.append(rect(pii_x, pii_y, pii_w, pii_h, fill=JADE_LIGHT, rx=8))
    parts.append(emoji(pii_x + 22, pii_y + pii_h / 2, "🛡", size=20))
    parts.append(text(pii_x + 44, pii_y + 22, "PII Edge",
                      size=12, color=CREAM, weight="bold"))
    parts.append(text(pii_x + 44, pii_y + 40, "spaCy + Indian NER",
                      size=10, color=CREAM))

    # VAD / Mic worker
    vad_x = pii_x + pii_w + 12; vad_y = pii_y; vad_w = pii_w; vad_h = pii_h
    parts.append(rect(vad_x, vad_y, vad_w, vad_h, fill=JADE_LIGHT, rx=8))
    parts.append(emoji(vad_x + 22, vad_y + vad_h / 2, "🎚", size=20))
    parts.append(text(vad_x + 44, vad_y + 22, "VAD · Noise",
                      size=12, color=CREAM, weight="bold"))
    parts.append(text(vad_x + 44, vad_y + 40, "WebAudio analyser",
                      size=10, color=CREAM))

    # arrow Internet GW -> FastAPI
    parts.append(arrow(ig_cx, ig_cy + 22, fa_x + fa_w / 2, fa_y,
                       color=JADE_DEEP, sw=2.5))

    # ----- private subnet (right) — services -----
    prv_x = pub_x + pub_w + 30; prv_y = pub_y
    prv_w = pub_w; prv_h = pub_h
    parts.append(rect(prv_x, prv_y, prv_w, prv_h, fill=WHITE,
                      stroke=PLUM_LIGHT, sw=1.5, rx=10, dash="5 3"))
    parts.append(lock_icon(prv_x + 12, prv_y + 8, color=PLUM))
    parts.append(text(prv_x + 36, prv_y + 18,
                      "Private Subnet · 10.0.2.0/24  ·  services",
                      size=11, color=PLUM, weight="bold"))

    # Conversation core
    conv_x = prv_x + 24; conv_y = prv_y + 38
    conv_w = prv_w - 48; conv_h = 70
    parts.append(rect(conv_x, conv_y, conv_w, conv_h, fill=PLUM, rx=8))
    parts.append(emoji(conv_x + 24, conv_y + conv_h / 2, "🤖", size=22))
    parts.append(text(conv_x + 50, conv_y + 22, "Conversation Core",
                      size=13, color=CREAM, weight="bold"))
    parts.append(text(conv_x + 50, conv_y + 42, "ask · verify · guide · close · hand-over",
                      size=10, color=CREAM))
    parts.append(text(conv_x + 50, conv_y + 58, "chat history · slots · last_question",
                      size=10, color=GOLD_LIGHT))

    # State machine
    sm_x = conv_x; sm_y = conv_y + conv_h + 10
    sm_w = (conv_w - 12) / 2; sm_h = 60
    parts.append(rect(sm_x, sm_y, sm_w, sm_h, fill=GOLD, rx=8))
    parts.append(emoji(sm_x + 22, sm_y + sm_h / 2, "🚦", size=20))
    parts.append(text(sm_x + 44, sm_y + 22, "State Machine",
                      size=12, color=INK, weight="bold"))
    parts.append(text(sm_x + 44, sm_y + 40, "VERIFIED · CLARIFY · HANDOVER",
                      size=10, color=INK_2))

    # Sentiment fusion
    sf_x = sm_x + sm_w + 12; sf_y = sm_y; sf_w = sm_w; sf_h = sm_h
    parts.append(rect(sf_x, sf_y, sf_w, sf_h, fill=GOLD, rx=8))
    parts.append(emoji(sf_x + 22, sf_y + sf_h / 2, "📊", size=20))
    parts.append(text(sf_x + 44, sf_y + 22, "Sentiment 6-D",
                      size=12, color=INK, weight="bold"))
    parts.append(text(sf_x + 44, sf_y + 40, "prosody + lexical fusion",
                      size=10, color=INK_2))

    # arrow FastAPI -> Conversation Core
    parts.append(arrow(fa_x + fa_w, fa_y + fa_h / 2,
                       conv_x, conv_y + conv_h / 2,
                       color=JADE_DEEP, sw=2.5))

    # ----- DB subnet (full-width row below) -----
    db_x = vpc_x + 30; db_y = pub_y + pub_h + 24
    db_w = vpc_w - 60; db_h = 165
    parts.append(rect(db_x, db_y, db_w, db_h, fill=WHITE,
                      stroke=GOLD, sw=1.5, rx=10, dash="5 3"))
    parts.append(lock_icon(db_x + 12, db_y + 8, color=GOLD))
    parts.append(text(db_x + 36, db_y + 18,
                      "Data Subnet · 10.0.3.0/24  ·  ledger + memory",
                      size=11, color=GOLD, weight="bold"))

    # PostgreSQL
    pg_x = db_x + 24; pg_y = db_y + 38; pg_w = (db_w - 72) / 3; pg_h = 110
    parts.append(rect(pg_x, pg_y, pg_w, pg_h, fill=JADE_DEEP, rx=8))
    parts.append(emoji(pg_x + pg_w / 2, pg_y + 28, "🗄", size=24))
    parts.append(text(pg_x + pg_w / 2, pg_y + 56, "PostgreSQL 16",
                      size=13, color=CREAM, weight="bold", anchor="middle"))
    parts.append(text(pg_x + pg_w / 2, pg_y + 76, "hash-chained audit ledger",
                      size=10, color=GOLD_LIGHT, anchor="middle"))
    parts.append(text(pg_x + pg_w / 2, pg_y + 94, "RTI-ready · tamper-evident",
                      size=10, color=CREAM, anchor="middle"))

    # Redis cache
    rd_x = pg_x + pg_w + 24; rd_y = pg_y; rd_w = pg_w; rd_h = pg_h
    parts.append(rect(rd_x, rd_y, rd_w, rd_h, fill=RED, rx=8))
    parts.append(emoji(rd_x + rd_w / 2, rd_y + 28, "⚡", size=24))
    parts.append(text(rd_x + rd_w / 2, rd_y + 56, "Redis · in-memory",
                      size=13, color=CREAM, weight="bold", anchor="middle"))
    parts.append(text(rd_x + rd_w / 2, rd_y + 76, "session · slots · chat history",
                      size=10, color=CREAM, anchor="middle"))
    parts.append(text(rd_x + rd_w / 2, rd_y + 94, "PII unredact map (TTL)",
                      size=10, color=GOLD_LIGHT, anchor="middle"))

    # Object store
    s3_x = rd_x + rd_w + 24; s3_y = pg_y; s3_w = pg_w; s3_h = pg_h
    parts.append(rect(s3_x, s3_y, s3_w, s3_h, fill=GREEN, rx=8))
    parts.append(emoji(s3_x + s3_w / 2, s3_y + 28, "🪣", size=24))
    parts.append(text(s3_x + s3_w / 2, s3_y + 56, "Object Store",
                      size=13, color=CREAM, weight="bold", anchor="middle"))
    parts.append(text(s3_x + s3_w / 2, s3_y + 76, "training queue · audio dumps",
                      size=10, color=CREAM, anchor="middle"))
    parts.append(text(s3_x + s3_w / 2, s3_y + 94, "anonymised dialect corpus",
                      size=10, color=GOLD_LIGHT, anchor="middle"))

    # arrow conversation core -> postgres
    parts.append(arrow(conv_x + conv_w / 2, conv_y + conv_h,
                       pg_x + pg_w / 2, pg_y,
                       color=PLUM, sw=2, head_id="arrow-plum"))
    # arrow conversation core -> redis (via small offset)
    parts.append(arrow(conv_x + conv_w * 0.75, conv_y + conv_h,
                       rd_x + rd_w / 2, rd_y,
                       color=PLUM, sw=2, head_id="arrow-plum"))

    # ====================================================
    # 3. RIGHT-HAND SERVICE COLUMN
    # ====================================================
    sb_x = cloud_x + cloud_w + 18
    sb_y = cloud_y
    sb_w = W - sb_x - 22
    parts.append(text(sb_x + 14, sb_y + 16,
                      "EXTERNAL SERVICES",
                      size=11, color=GOLD, weight="bold"))

    services = [
        ("Groq Whisper-Large-v3", "STT · sub-second", JADE,
         "verbose_json segments  ·  prompt-priming  ·  no-speech recovery"),
        ("Groq Llama-3.3-70B",     "Conversation LLM",  PLUM,
         "STRICT JSON  ·  ask·verify·guide·close·handover  ·  16-turn history"),
        ("edge-tts (Microsoft)",   "TTS · dialect voice", GOLD,
         "kn-IN  ·  hi-IN  ·  en-IN voices  ·  streaming MP3"),
        ("Telegram Bot API",       "Hand-over notifier", RED,
         "officer ping with full context  ·  free tier"),
        ("AI4Bharat / BharatGen",   "Future Indic models", GREEN,
         "IndicConformer · Shrutam2 · Sooktam2  ·  ready-to-swap"),
    ]
    yy = sb_y + 36
    for title, sub, color, body in services:
        parts.append(rect(sb_x, yy, sb_w, 92, fill=WHITE, stroke=color, sw=1.5, rx=10))
        parts.append(rect(sb_x, yy, 6, 92, fill=color, rx=0))
        parts.append(text(sb_x + 16, yy + 16, title, size=12, color=color, weight="bold"))
        parts.append(text(sb_x + 16, yy + 32, sub, size=10, color=INK_2, weight="bold"))
        # body wrap
        wrapped = textwrap.wrap(body, width=34)
        for j, line in enumerate(wrapped[:3]):
            parts.append(text(sb_x + 16, yy + 52 + j * 14, line, size=9.5, color=INK_2))
        yy += 102

    # dashed connectors from FastAPI / Conv-core to the sidebar (just a
    # visual hint they reach out)
    for i in range(2):
        y_target = sb_y + 36 + i * 102 + 46
        parts.append(f'<line x1="{cloud_x + cloud_w}" y1="{y_target}" '
                     f'x2="{sb_x}" y2="{y_target}" stroke="{GOLD}" '
                     f'stroke-width="1.5" stroke-dasharray="4 3" '
                     f'marker-end="url(#arrow-gold)"/>')

    # ====================================================
    # 4. CITIZEN + AGENT (left/right of cloud)
    # ====================================================
    # citizen
    cit_x = cloud_x; cit_y = cloud_y - 12
    parts.append(rect(cit_x - 100, cit_y + 200, 90, 80, fill=WHITE,
                      stroke=JADE_DEEP, sw=1.5, rx=10))
    parts.append(emoji(cit_x - 55, cit_y + 230, "📞", size=28))
    parts.append(text(cit_x - 55, cit_y + 270, "Citizen", size=11, color=JADE_DEEP,
                      weight="bold", anchor="middle"))
    parts.append(arrow(cit_x - 10, cit_y + 240, cit_x + 12, cit_y + 240,
                       color=JADE_DEEP, sw=2.5))

    # ====================================================
    # 5. BOTTOM ROW — Request Flow + Key Properties + Sovereignty
    # ====================================================
    by = cloud_y + cloud_h + 18
    bh = 160

    # Request Flow card
    rf_x = 20; rf_w = 600
    parts.append(rect(rf_x, by, rf_w, bh, fill=WHITE, stroke=LINE, sw=1.2, rx=10))
    parts.append(rect(rf_x, by, rf_w, 26, fill=JADE_DEEP, rx=10))
    parts.append(text(rf_x + 16, by + 13, "REQUEST FLOW", size=12, color=CREAM, weight="bold"))

    rf_steps = [
        ("📞", "Citizen"),
        ("🌐", "Browser"),
        ("⚡", "FastAPI"),
        ("🛡", "PII Edge"),
        ("🎙", "Whisper"),
        ("🤖", "LLM"),
        ("🔊", "TTS"),
    ]
    rfx = rf_x + 28; rfy = by + 70
    box_w = 60; box_h = 60; gap = 18
    for i, (ic, lab) in enumerate(rf_steps):
        x = rfx + i * (box_w + gap)
        parts.append(rect(x, rfy, box_w, box_h, fill=CREAM, stroke=JADE, sw=1.2, rx=8))
        parts.append(emoji(x + box_w / 2, rfy + 24, ic, size=22))
        parts.append(text(x + box_w / 2, rfy + 50, lab, size=9, color=INK_2,
                          weight="bold", anchor="middle"))
        if i < len(rf_steps) - 1:
            parts.append(arrow(x + box_w + 2, rfy + box_h / 2,
                                x + box_w + gap - 2, rfy + box_h / 2,
                                color=JADE_DEEP, sw=2))
    parts.append(text(rf_x + 16, by + 144,
                      "Round-trip p50 ≤ 0.75 s  ·  p95 ≤ 1.6 s",
                      size=10, color=GOLD, weight="bold"))

    # Key Properties
    kp_x = rf_x + rf_w + 16; kp_w = 480
    parts.append(rect(kp_x, by, kp_w, bh, fill=WHITE, stroke=LINE, sw=1.2, rx=10))
    parts.append(rect(kp_x, by, kp_w, 26, fill=GOLD, rx=10))
    parts.append(text(kp_x + 16, by + 13, "KEY PROPERTIES", size=12, color=INK, weight="bold"))

    props = [
        "ALL inference runs in-jurisdiction — no foreign API on the critical path.",
        "ASR script-locked to Karnataka — Gujarati/Marathi hallucinations rejected.",
        "Conversational LLM uses full chat history + last_question for yes/no.",
        "Distress fast-path force-triggers human handover regardless of confidence.",
        "Every state transition writes a hash-chained audit row (RTI-ready).",
        "Citizen TTS is dialect-conditioned — caller hears their own register.",
    ]
    for i, line in enumerate(props):
        parts.append(text(kp_x + 16, by + 50 + i * 17, "✓  " + line,
                          size=10.5, color=INK_2))

    # Sovereignty / Hand-over Path
    sv_x = kp_x + kp_w + 16; sv_w = W - sv_x - 22
    parts.append(rect(sv_x, by, sv_w, bh, fill=WHITE, stroke=LINE, sw=1.2, rx=10))
    parts.append(rect(sv_x, by, sv_w, 26, fill=PLUM, rx=10))
    parts.append(text(sv_x + 16, by + 13, "HAND-OVER PATH", size=12, color=CREAM, weight="bold"))

    ho_steps = [
        ("Trigger", "severe issue · distress rising · user ask"),
        ("Bridge",  "warm TTS line in citizen's dialect (≤ 4s)"),
        ("Pre-load", "agent dashboard fills with running interpretation"),
        ("Take-over", "officer steps in mid-call · AI silent"),
    ]
    for i, (lab, body) in enumerate(ho_steps):
        y = by + 44 + i * 28
        parts.append(rect(sv_x + 16, y, 22, 22, fill=PLUM, rx=11))
        parts.append(text(sv_x + 27, y + 11, str(i + 1),
                          size=11, color=CREAM, weight="bold", anchor="middle"))
        parts.append(text(sv_x + 50, y + 8, lab, size=10.5, color=PLUM, weight="bold"))
        parts.append(text(sv_x + 50, y + 22, body, size=10, color=INK_2))

    # Footer
    parts.append(text(W / 2, H - 14,
                      "Pratyaya · architecture diagram · Team Arjuna",
                      size=10, color=MUTED, anchor="middle"))

    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'style="background:{CREAM_2};font-family:Segoe UI, Calibri, Inter, sans-serif">'
            + "\n".join(parts) + "</svg>")


def main():
    out_dir = Path(__file__).resolve().parent.parent
    svg_path = out_dir / "architecture.svg"
    svg_path.write_text(build_svg(), encoding="utf-8")
    size_kb = svg_path.stat().st_size // 1024
    print(f"[ok] wrote {svg_path}  ({size_kb} KB)")

    # try a PNG render — Cairo first (best quality), then svglib (pure Python).
    png_path = out_dir / "architecture.png"
    rendered = False
    try:
        import cairosvg
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path),
                         output_width=2400)
        rendered = True
    except Exception:
        pass

    if not rendered:
        try:
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPM
            drawing = svg2rlg(str(svg_path))
            renderPM.drawToFile(drawing, str(png_path), fmt="PNG", dpi=180)
            rendered = True
        except Exception as e:
            print(f"[info] PNG via svglib failed: {type(e).__name__}: {e}")

    if rendered:
        kb = png_path.stat().st_size // 1024
        print(f"[ok] wrote {png_path}  ({kb} KB)")
    else:
        print("[info] PNG render skipped — open architecture.svg in any "
              "browser and screenshot, or open it in Inkscape to export a PNG.")


if __name__ == "__main__":
    main()
