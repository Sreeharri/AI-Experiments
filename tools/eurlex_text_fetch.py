"""Fetch the text of an EUR-Lex act from the official website when the database has none.

Used by export_eurlex_changes.py with --fetch-missing, for corrigenda and for changing acts without stored text.
Only the official host eur-lex.europa.eu is contacted; every page is cached, so a later run does not fetch again.

    text, status = fetch_text("32016R0679R(02)", "DE", cache_dir)   # status: OK | MISSING | ERROR
    elements = split_elements(text)                                  # [{"identifier", "heading", "text"}]
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = "eur-lex.europa.eu"
URL = "https://eur-lex.europa.eu/legal-content/{lang}/TXT/HTML/?uri=CELEX:{celex}"
MAX_BYTES = 20 * 1024 * 1024
PAUSE_SECONDS = 1.0
NOT_FOUND = re.compile(r"requested document does not exist|angeforderte Dokument existiert nicht|"
                       r"Das angeforderte Dokument ist nicht vorhanden", re.I)
LEGAL_TEXT = re.compile(r"Official Journal|Amtsblatt|\bArticle\s+1\b|\bArtikel\s+1\b|Corrigendum|Berichtigung|"
                        r"\bfor:|\bAnstatt\b", re.I)
BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "td", "th"}
_last_request = [0.0]


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).hostname != HOST or urlsplit(newurl).scheme != "https":
            raise URLError("redirect outside eur-lex.europa.eu")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.skip += 1
        elif tag in BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"} and self.skip:
            self.skip -= 1
        elif tag in BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def fetch_text(celex: str, lang: str, cache_dir: Path, opener=None) -> tuple[str, str]:
    """(text, status). status OK, MISSING (EUR-Lex has no such language version) or ERROR (no answer)."""
    cache = Path(cache_dir) / lang.upper() / f"{re.sub(r'[^A-Za-z0-9]+', '_', celex)}.txt"
    if cache.is_file():
        text = cache.read_text(encoding="utf-8")
        return (text, "OK") if text != "<MISSING>" else ("", "MISSING")
    url = URL.format(lang=lang.upper(), celex=quote(celex, safe=""))
    wait = PAUSE_SECONDS - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "LegalRadar/1.0 (internal research)", "Accept-Language": lang})
        with (opener or build_opener(_Redirects())).open(request, timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return "", "ERROR"
        html = raw.decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code == 404:
            html = "<MISSING>"
        else:
            return "", "ERROR"
    except (URLError, OSError, TimeoutError):
        return "", "ERROR"
    text = "<MISSING>" if html == "<MISSING>" or NOT_FOUND.search(html[:20000]) else html_to_text(html)
    if text != "<MISSING>" and not LEGAL_TEXT.search(text):
        return "", "ERROR"       # a check page or an error page, not the act: not cached, tried again next time
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return (text, "OK") if text != "<MISSING>" else ("", "MISSING")


HEADING = re.compile(r"^(Article|Artikel)\s+(\d+[a-z]*)\s*$|^(ANNEX|ANHANG|Annex|Anhang)(\s+[IVXLC0-9]+[a-z]*)?\s*$")


def split_elements(text: str) -> list[dict]:
    """Articles and annexes of a fetched act: a line "Article 5" / "ANNEX II" starts a new element."""
    elements, current = [], None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = HEADING.match(line.strip())
        if match:
            if current:
                elements.append(current)
            if match.group(1):
                identifier = f"{'Article' if match.group(1) == 'Article' else 'Artikel'} {match.group(2)}"
            else:
                word = "Annex" if match.group(3).casefold() == "annex" else "Anhang"
                identifier = f"{word}{(' ' + match.group(4).strip()) if match.group(4) else ''}"
            heading = lines[index + 1].strip() if index + 1 < len(lines) and not HEADING.match(lines[index + 1].strip()) else None
            current = {"identifier": identifier, "heading": heading, "text": identifier}
        elif current:
            current["text"] += " " + line.strip()
    if current:
        elements.append(current)
    if not elements and text.strip():
        elements = [{"identifier": "Text", "heading": None, "text": re.sub(r"\s+", " ", text).strip()}]
    return elements
