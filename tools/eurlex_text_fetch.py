"""Fetch the text of an EUR-Lex act from the official EU websites when the database has none.

Used by export_eurlex_changes.py with --fetch-missing, for corrigenda and for changing acts without stored text.
Two official sources are tried, both run by the EU Publications Office:
  1. eur-lex.europa.eu   the HTML page of the act
  2. publications.europa.eu (Cellar)   the same document through the Publications Office's download service
Every text found is cached, so a later run does not fetch it again. Errors are not cached (tried again next time).

Test one act by hand (prints what each source answered):
    python tools/eurlex_text_fetch.py "32016R0185R(01)" DE
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOSTS = {"eur-lex.europa.eu", "publications.europa.eu"}
EURLEX_URL = "https://eur-lex.europa.eu/legal-content/{lang}/TXT/HTML/?uri=CELEX:{celex}"
CELLAR_URL = "https://publications.europa.eu/resource/celex/{celex}"
CELLAR_LANG = {"DE": "deu", "EN": "eng"}
MAX_BYTES = 20 * 1024 * 1024
PAUSE_SECONDS = 1.0
NOT_FOUND = re.compile(r"requested document does not exist|angeforderte Dokument existiert nicht|"
                       r"Das angeforderte Dokument ist nicht vorhanden", re.I)
LEGAL_TEXT = re.compile(r"Official Journal|Amtsblatt|\bArticle\s+1\b|\bArtikel\s+1\b|Corrigendum|Berichtigung|"
                        r"\bfor:|\bAnstatt\b", re.I)
BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "td", "th"}
_last_request = [0.0]
last_attempts: list[str] = []      # what each source answered for the latest fetch (for the hand test)


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).hostname not in HOSTS or urlsplit(newurl).scheme != "https":
            raise URLError(f"redirect outside the official hosts: {urlsplit(newurl).hostname}")
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


def _get(url: str, headers: dict, opener) -> tuple[str, str]:
    """(html, status): status OK, MISSING (404 / not found page) or 'ERROR <reason>'."""
    wait = PAUSE_SECONDS - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "LegalRadar/1.0 (internal research)", **headers})
        with (opener or build_opener(_Redirects())).open(request, timeout=40) as response:
            raw = response.read(MAX_BYTES + 1)
            code = getattr(response, "status", 200)
    except HTTPError as exc:
        return "", "MISSING" if exc.code in (404, 410) else f"ERROR HTTP {exc.code}"
    except (URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return "", f"ERROR {type(exc).__name__}: {str(reason)[:160]}"
    if len(raw) > MAX_BYTES:
        return "", "ERROR too large"
    if code == 202 or (len(raw) < 5000 and b"challenge" in raw.lower()):
        return "", f"ERROR HTTP {code}: a check page (bot protection), not the document"
    html = raw.decode("utf-8", errors="replace")
    if NOT_FOUND.search(html[:20000]):
        return "", "MISSING"
    return html, "OK"


def fetch_text(celex: str, lang: str, cache_dir: Path, opener=None) -> tuple[str, str]:
    """(text, status). status OK, MISSING (no such language version anywhere) or ERROR (no usable answer)."""
    lang = lang.upper()
    cache = Path(cache_dir) / lang / f"{re.sub(r'[^A-Za-z0-9]+', '_', celex)}.txt"
    if cache.is_file():
        text = cache.read_text(encoding="utf-8")
        return (text, "OK") if text != "<MISSING>" else ("", "MISSING")
    last_attempts.clear()
    sources = [("EUR-Lex", EURLEX_URL.format(lang=lang, celex=quote(celex, safe="")), {"Accept-Language": lang.lower()}),
               ("Cellar", CELLAR_URL.format(celex=quote(celex, safe="")),
                {"Accept": "application/xhtml+xml, text/html;q=0.9", "Accept-Language": CELLAR_LANG.get(lang, lang)})]
    statuses = []
    for name, url, headers in sources:
        html, status = _get(url, headers, opener)
        if status == "OK":
            text = html_to_text(html)
            if not LEGAL_TEXT.search(text):
                status = "ERROR the page holds no legal text"
            else:
                last_attempts.append(f"{name}: OK")
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding="utf-8")
                return text, "OK"
        last_attempts.append(f"{name}: {status}")
        statuses.append(status)
    if all(status == "MISSING" for status in statuses):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("<MISSING>", encoding="utf-8")
        return "", "MISSING"
    return "", "ERROR"


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


def main(argv=None) -> int:
    args = (argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__)
        return 1
    celex, lang = args[0], (args[1] if len(args) > 1 else "EN")
    import tempfile
    with tempfile.TemporaryDirectory() as folder:      # no cache: always asks the websites
        text, status = fetch_text(celex, lang, Path(folder))
    for line in last_attempts:
        print(line)
    print(f"Result: {status}")
    if text:
        print(text[:600])
    return 0 if status == "OK" else 2


if __name__ == "__main__":
    sys.exit(main())
