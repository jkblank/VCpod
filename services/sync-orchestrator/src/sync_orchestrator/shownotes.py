"""Show notes arrive as HTML from RSS feeds, but the iPod shows the
"Description Text" field as plain text. Converted here, at the device
boundary, so the stored description keeps the feed's original markup."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_LINE_TAGS = frozenset({"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"})
_SKIPPED_TAGS = frozenset({"script", "style"})


class _PlaintextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in _LINE_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _LINE_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        return "".join(self._parts)


def html_to_plaintext(text: str) -> str:
    if not text:
        return ""
    extractor = _PlaintextExtractor()
    extractor.feed(text)
    extractor.close()
    lines = [" ".join(line.split()) for line in extractor.text().splitlines()]
    joined = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()
