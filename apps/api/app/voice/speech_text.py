"""Turning the model's text into what the TTS should say, and cutting it into sentences as it streams.

Kokoro reads "R$ 180" as "real dollar 180", "8h30" as "oito H30" and, in English, "Dra." as "draw".
Spelled-out forms come out right, so replies are rewritten before synthesis.
"""

import re

_TITLES = {
    "pt": {"Dra.": "Doutora", "Dr.": "Doutor", "Sra.": "Senhora", "Sr.": "Senhor"},
    "en": {"Dra.": "Doctor", "Dr.": "Doctor", "Sra.": "Mrs.", "Sr.": "Mr."},
}
_MONEY = re.compile(r"R\$\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{2}))?")
_PT_CLOCK = re.compile(r"\b(\d{1,2})h(\d{2})\b")
_MARKUP = re.compile(r"[*_#`~>|]+")


def _money(match: re.Match) -> str:
    reais = match.group(1).replace(".", "")
    cents = match.group(2)
    return f"{reais} reais" + (f" e {int(cents)} centavos" if cents and int(cents) else "")


def _pt_clock(match: re.Match) -> str:
    hour, minute = match.group(1), int(match.group(2))
    return f"{hour} e meia" if minute == 30 else f"{hour} e {minute}"


def speakable(text: str, language: str) -> str:
    text = _MARKUP.sub("", text)
    for title, spoken in _TITLES["en" if language == "en" else "pt"].items():
        text = re.sub(rf"\b{re.escape(title)}", spoken, text)
    text = _MONEY.sub(_money, text)
    if language != "en":
        text = _PT_CLOCK.sub(_pt_clock, text)
    return re.sub(r"\s+", " ", text).strip()


# A period after these doesn't end a sentence
_ABBREVIATIONS = {"dr", "dra", "sr", "sra", "srta", "prof", "profa", "etc", "a.m", "p.m", "av", "n"}
_BOUNDARY = re.compile(r"[.!?…]+[\"'”)\]]*(?=\s)")


class SentenceSplitter:
    """Collects streamed text and hands back each sentence once it's complete, so speech can start early."""

    def __init__(self) -> None:
        self.buffer = ""

    def push(self, delta: str) -> list[str]:
        self.buffer += delta
        sentences, start = [], 0
        for match in _BOUNDARY.finditer(self.buffer):
            words = self.buffer[start : match.start()].split()
            if match.group().startswith(".") and words and words[-1].lower().rstrip(".") in _ABBREVIATIONS:
                continue
            sentence = self.buffer[start : match.end()].strip()
            if sentence:
                sentences.append(sentence)
            start = match.end()
        self.buffer = self.buffer[start:]
        return sentences

    def flush(self) -> str | None:
        rest, self.buffer = self.buffer.strip(), ""
        return rest or None
