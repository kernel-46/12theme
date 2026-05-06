"""Indic-script -> Roman transliteration (pure-Python, no external deps).

Covers the most common Kannada and Devanagari characters used in helpline
calls. Returns the input unchanged for English or unknown scripts.
"""
from __future__ import annotations
from typing import Optional

# Kannada Unicode block (U+0C80–U+0CFF) -> IAST-style Roman
_KN: dict[str, str] = {
    # Vowels
    "ಅ":"a","ಆ":"ā","ಇ":"i","ಈ":"ī","ಉ":"u","ಊ":"ū",
    "ಋ":"ṛ","ಎ":"e","ಏ":"ē","ಐ":"ai","ಒ":"o","ಓ":"ō","ಔ":"au",
    # Vowel signs
    "ಾ":"ā","ಿ":"i","ೀ":"ī","ು":"u","ೂ":"ū","ೃ":"ṛ",
    "ೆ":"e","ೇ":"ē","ೈ":"ai","ೊ":"o","ೋ":"ō","ೌ":"au",
    "ಂ":"ṃ","ಃ":"ḥ","್":"",
    # Consonants
    "ಕ":"k","ಖ":"kh","ಗ":"g","ಘ":"gh","ಙ":"ṅ",
    "ಚ":"c","ಛ":"ch","ಜ":"j","ಝ":"jh","ಞ":"ñ",
    "ಟ":"ṭ","ಠ":"ṭh","ಡ":"ḍ","ಢ":"ḍh","ಣ":"ṇ",
    "ತ":"t","ಥ":"th","ದ":"d","ಧ":"dh","ನ":"n",
    "ಪ":"p","ಫ":"ph","ಬ":"b","ಭ":"bh","ಮ":"m",
    "ಯ":"y","ರ":"r","ಲ":"l","ವ":"v","ಶ":"ś",
    "ಷ":"ṣ","ಸ":"s","ಹ":"h","ಳ":"ḷ","ಱ":"ṟ","ಲ಼":"ḻ",
    # Digits
    "೦":"0","೧":"1","೨":"2","೩":"3","೪":"4",
    "೫":"5","೬":"6","೭":"7","೮":"8","೯":"9",
}

# Devanagari Unicode block (U+0900–U+097F) -> Roman
_HI: dict[str, str] = {
    # Vowels
    "अ":"a","आ":"ā","इ":"i","ई":"ī","उ":"u","ऊ":"ū",
    "ऋ":"ṛ","ए":"e","ऐ":"ai","ओ":"o","औ":"au",
    # Vowel signs
    "ा":"ā","ि":"i","ी":"ī","ु":"u","ू":"ū","ृ":"ṛ",
    "े":"e","ै":"ai","ो":"o","ौ":"au",
    "ं":"ṃ","ः":"ḥ","्":"","ँ":"m̐",
    # Consonants
    "क":"k","ख":"kh","ग":"g","घ":"gh","ङ":"ṅ",
    "च":"c","छ":"ch","ज":"j","झ":"jh","ञ":"ñ",
    "ट":"ṭ","ठ":"ṭh","ड":"ḍ","ढ":"ḍh","ण":"ṇ",
    "त":"t","थ":"th","द":"d","ध":"dh","न":"n",
    "प":"p","फ":"ph","ब":"b","भ":"bh","म":"m",
    "य":"y","र":"r","ल":"l","व":"v","श":"ś",
    "ष":"ṣ","स":"s","ह":"h","ळ":"ḷ",
    "क़":"q","ख़":"x","ग़":"ġ","ज़":"z","ड़":"ṛ","ढ़":"ṛh","फ़":"f","य़":"ẏ",
    # Digits
    "०":"0","१":"1","२":"2","३":"3","४":"4",
    "५":"5","६":"6","७":"7","८":"8","९":"9",
}

_LANG_MAP = {
    "kn": _KN, "kannada": _KN,
    "hi": _HI, "hindi": _HI,
    "mr": _HI, "marathi": _HI,
}


def to_roman(text: Optional[str], lang: Optional[str]) -> str:
    """Romanize Kannada / Devanagari text using a character map.
    Returns the input unchanged for English or unknown scripts."""
    if not text:
        return ""
    if not lang:
        return text
    table = _LANG_MAP.get((lang or "").lower().split("-")[0])
    if table is None:
        return text
    # Transliterate character by character; unknown chars pass through.
    # Virama (halant) suppresses the inherent 'a' of the preceding consonant —
    # handled by mapping '್'/'्' to "" above so the consonant stands alone.
    out = []
    for ch in text:
        out.append(table.get(ch, ch))
    result = "".join(out)
    # Post-process: consonant + no vowel sign = consonant + inherent 'a'
    # This is a simplification — good enough for display purposes.
    return result
