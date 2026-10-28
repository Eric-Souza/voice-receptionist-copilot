"""Turning the model's text into what the TTS should say, and cutting it into sentences as it streams.

Symbols and abbreviations ("$1,200.50", "Dr.") are spelled out before synthesis, so Kokoro doesn't have to
guess how to read them.
"""

import re

_TITLES = {"Dr.": "Doctor"}
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d)")
# Models write "8\u202fam" with a narrow no-break space; Kokoro's phonemizer only knows plain spaces
_SPACES = re.compile("[\u00a0\u2007\u2009\u202f]")
_MARKUP = re.compile(r"[*_#`~>|]+")


def _money(match: re.Match) -> str:
    dollars = int(match.group(1).replace(",", ""))
    cents = int((match.group(2) or "0").ljust(2, "0"))
    spoken = f"{dollars} dollar{'' if dollars == 1 else 's'}"
    return spoken + (f" and {cents} cent{'' if cents == 1 else 's'}" if cents else "")


def speakable(text: str) -> str:
    text = _SPACES.sub(" ", _MARKUP.sub("", text))
    for title, spoken in _TITLES.items():
        text = re.sub(rf"\b{re.escape(title)}", spoken, text)
    text = _MONEY.sub(_money, text)
    return re.sub(r"\s+", " ", text).strip()


# A period after these doesn't end a sentence
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "ave", "prof", "etc", "a.m", "p.m", "vs"}
_BOUNDARY = re.compile(r"[.!?…]+[\"'”)\]]*(?=\s)")


_CLAUSE = re.compile(r"[,;:](?=\s)")


class SentenceSplitter:
    """Collects streamed text and hands back each sentence once it's complete, so speech can start early.

    The caller is waiting for the first piece, and synthesis time grows with its length, so a long first
    sentence is cut at its first comma once that comes at least `first_clause` characters in.
    """

    def __init__(self, first_clause: int = 25) -> None:
        self.buffer = ""
        self.first_clause = first_clause
        self.started = False

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
        if not sentences and not self.started:
            clause = _CLAUSE.search(self.buffer, self.first_clause)
            if clause:
                sentences.append(self.buffer[: clause.end()].strip())
                self.buffer = self.buffer[clause.end() :]
        self.started = self.started or bool(sentences)
        return sentences

    def flush(self) -> str | None:
        rest, self.buffer = self.buffer.strip(), ""
        return rest or None
