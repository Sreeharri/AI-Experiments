"""Check a target's stored text before it is sent to the model.

Some stored "articles" and "annexes" are not provisions at all: a cell of a
correlation table, a line of a table of contents, a picture placeholder.
Those are reported as not assessable and are never sent to the model.
Text that starts or ends mid-sentence is still sent, with a warning.
"""

import re

# Skipped: nothing here can be classified.
NO_TEXT = "NO_TEXT"
REFERENCE_ONLY = "REFERENCE_ONLY"
TABLE_OF_CONTENTS = "TABLE_OF_CONTENTS"
IMAGE_ONLY = "IMAGE_ONLY"
PDF_PAGE_TABLE = "PDF_PAGE_TABLE"
# Sent with a warning.
STARTS_MID_SENTENCE = "STARTS_MID_SENTENCE"
ENDS_MID_SENTENCE = "ENDS_MID_SENTENCE"

WARNING_TEXT = {
    STARTS_MID_SENTENCE: "The stored text of the target starts in the middle of a sentence. "
                         "Its beginning is missing.",
    ENDS_MID_SENTENCE: "The stored text of the target is cut off in the middle of a sentence. "
                       "Its end is missing.",
}

REFERENCE_WORDS = frozenset("""
    article articles annex annexes section sections paragraph paragraphs point points part parts
    table tables chapter chapters title subparagraph indent sentence and or to of the in this that
    regulation directive decision no ec eec eu euratom first second third fourth fifth
    artikel anhang anhänge absatz abschnitt nummer buchstabe teil tabelle kapitel satz und oder
    bis der die das des dieser verordnung richtlinie beschluss nr eg ewg
""".split())
ROMAN = re.compile(r"[IVXLCDM]+", re.I)
WORD = re.compile(r"[^\W\d_]{2,}")
DOT_LEADER = re.compile(r"\.{4,}\s*\d+")
PICTURE = re.compile(r">\s*PIC FILE\s*=\s*\"?[^\">]*\"?\s*>", re.I)
SENTENCE_VERBS = frozenset("is are shall was were has have may must will wird werden ist sind".split())
ENDING_WORDS = frozenset("in of the to and or a an by for with that which as at on from under".split())


def body_text(texts, label, heading=None):
    """Join the target text and remove its own label and heading from the start."""
    body = " ".join(" ".join(texts).split())
    lead = [label, *(part for part in (heading or "").split(" / "))]
    lead = [" ".join(item.split()) for item in lead if item and item.strip()]
    changed = True
    while changed:
        changed = False
        for item in lead:
            if body.casefold().startswith(item.casefold()):
                body = body[len(item):].lstrip(" .:-–—/")
                changed = True
    return body


def content_words(body):
    return [word for word in WORD.findall(body)
            if word.casefold() not in REFERENCE_WORDS and not ROMAN.fullmatch(word)]


def assess(kind, label, texts, heading=None, table_origin=None, single_text=True):
    """Return (skip, warnings). skip is None or {"code", "detail"}."""
    if kind == "TABLE" and table_origin == "PDF_PAGE_GRID":
        return {"code": PDF_PAGE_TABLE,
                "detail": "Unverified page table from a PDF. Its text is assessed with the annex."}, []
    body = body_text(texts, label, heading)
    if not re.search(r"\w", body):
        return {"code": NO_TEXT, "detail": "The target has no text besides its own label."}, []

    pictures = len(PICTURE.findall(body))
    body = PICTURE.sub(" ", body)
    words = WORD.findall(body)
    content = content_words(body)
    if pictures and len(content) <= 25:
        return {"code": IMAGE_ONLY,
                "detail": f"The target holds {pictures} picture placeholder(s) and no usable text."}, []
    leaders = len(DOT_LEADER.findall(body))
    if leaders and len(content) <= 15 * leaders:
        return {"code": TABLE_OF_CONTENTS,
                "detail": "The target is a table-of-contents line with a page number."}, []
    has_reference_word = any(word.casefold() in REFERENCE_WORDS for word in words)
    # A sentence such as "Articles 3 and 4 shall apply." has words of its own and is sent.
    if not content and has_reference_word:
        return {"code": REFERENCE_ONLY,
                "detail": "The target only names other provisions and has no rule of its own."}, []
    if len(words) >= 30 and len(content) < 0.08 * len(words):
        return {"code": REFERENCE_ONLY,
                "detail": "The target is a list of provision numbers, such as a correlation table."}, []

    warnings = []
    if kind in ("ARTICLE", "ANNEX") and single_text:
        first = WORD.search(body)
        opening = body[:first.start()] if first else ""
        # "Annex III is replaced by ..." is a full sentence whose subject is the label.
        if (first and first.group()[0].islower() and first.group() not in SENTENCE_VERBS
                and not re.search(r"[‘“\"'«„]", opening)):
            warnings.append(STARTS_MID_SENTENCE)
        if kind == "ARTICLE" and len(body) < 600 and words and words[-1].casefold() in ENDING_WORDS \
                and not re.search(r"[.;:!?)\]]\s*$", body):
            warnings.append(ENDS_MID_SENTENCE)
    return None, warnings
