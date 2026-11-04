"""Read one act at a time from the existing catalogue and language JSONL files."""
import json
from pathlib import Path
import re

CELEX = re.compile(r"3\d{4}[A-Z]\d{4}(?:\(\d{2}\))?")


def validate_celex(value):
    if not isinstance(value, str) or not CELEX.fullmatch(value):
        raise ValueError(f"Invalid CELEX ID: {value!r}")
    return value


class Catalogue:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.paths = [self.folder / "acts.jsonl"]
        self.paths += [self.folder / f"text_{lang}.jsonl" for lang in ("en", "de")
                       if (self.folder / f"text_{lang}.jsonl").exists()]
        if len(self.paths) == 1:
            raise ValueError("No text_en.jsonl or text_de.jsonl found")
        self.offsets = {}
        for path in self.paths:
            offsets = {}
            with path.open("rb") as stream:
                while True:
                    offset = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    celex = validate_celex(row["celex"])
                    if celex in offsets:
                        raise ValueError(f"Duplicate CELEX {celex} in {path.name}")
                    offsets[celex] = offset
            self.offsets[path.name] = offsets

    def ids(self):
        return list(self.offsets["acts.jsonl"])

    def read(self, name, celex):
        offset = self.offsets.get(name, {}).get(celex)
        if offset is None:
            return None
        with (self.folder / name).open("rb") as stream:
            stream.seek(offset)
            return json.loads(stream.readline())

    def get(self, celex):
        meta = self.read("acts.jsonl", celex)
        text = None
        for lang in ("en", "de"):
            candidate = self.read(f"text_{lang}.jsonl", celex)
            if candidate and any(candidate.get(k) for k in ("articles", "annexes", "document_text")):
                text = candidate
                break
        return adapt(meta, text or {})


def _label(item):
    return item.get("en") or item.get("de") or item.get("id") or ""


def document_fields(meta):
    """Map an acts.jsonl record to the document fields document_filter reads.

    acts.jsonl stores titles as {"en", "de"}, addressees, family links and legal basis
    under its own names; without this the model would get no title at all.
    """
    title = meta.get("title") or {}
    dates = meta.get("dates") or {}
    kinds = [_label(x) for x in meta.get("resource_type_label") or [] if _label(x)]
    family = meta.get("family") or {}
    return {
        **meta,
        "title_en": title.get("en") or meta.get("title_en"),
        "title_de": title.get("de") or meta.get("title_de"),
        "document_date": dates.get("document"),
        "act_type": kinds[0] if kinds else meta.get("act_type"),
        "addressed_to": [_label(x)[:120] for x in meta.get("addressees") or []],
        "changes": [{"relation": c.get("relation"), "celex": c.get("celex"),
                     "title": (c.get("title_en") or c.get("title_de") or "")[:200]}
                    for c in (family.get("changes") or [])[:5]],
        "legal_basis": [x.get("label") or (x.get("title_en") or x.get("title_de") or x.get("celex") or "")[:200]
                        for x in meta.get("legal_basis") or []],
    }


def adapt(meta, text):
    version = text.get("version") or {}
    elements = []
    for index, row in enumerate(text.get("outline") or []):
        elements.append({"path": f"outline/{index}", "element_type": row.get("type", "CHAPTER"),
                         "heading": row.get("heading", ""), "body_text": "", "details": {}})
    for key, kind in (("articles", "ARTICLE"), ("annexes", "ANNEX"), ("recitals", "RECITAL")):
        for index, row in enumerate(text.get(key) or [], 1):
            number = row.get("n")
            elements.append({
                "path": f"{kind.lower()}/{number or index}/row{index}",
                "element_type": kind, "number": number, "heading": row.get("heading") or "",
                "body_text": row.get("text") or "", "details": {"aggregate_body": True},
                "truncated": bool(row.get("truncated")),
            })
    if text.get("document_text"):
        elements.append({"path": "document-text", "element_type": "DOCUMENT_TEXT",
                         "heading": "", "body_text": text["document_text"],
                         "details": {"aggregate_body": True}})
    key = version.get("key")
    document = document_fields(meta)
    return {
        "celex": meta["celex"], "document": document,
        "instrument": {"title": document["title_en"] or document["title_de"] or meta.get("canonical_title")},
        "version": {"id": key, "version_key": key, "version_date": version.get("date"),
                    "language": text.get("language"), "quality_status": version.get("quality")},
        "elements": elements, "recitals_version": text.get("recitals_version"),
        "input_warnings": ["Some exported excerpts are truncated"] if any(e.get("truncated") for e in elements) else [],
    }
