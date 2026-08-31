"""
Getting a copy of your own records out.

Three formats and one destination rule. The formats are a PDF to read, a CSV to
work with, and a JSON for anyone who wants the full shape. The rule is that
nothing is sent to an external destination: a person can download a copy in
their browser, but there is no background sync, scheduled upload or stored
recipient.

Wording, deliberately: **a structured self-reported activity record for personal
reflection and optional sharing.** It is what someone said they did, in their
own words, with the times they chose to give.
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from contract import canonical_clock

COLUMNS = ("recorded_at", "occurred_at", "duration_minutes",
           "activity_text", "source_text", "transcript",
           "activity_domain", "labour_kind", "policy_result", "review_status",
           "supersedes", "provenance", "model_version",
           "prompt_version", "policy_version")

CLAIM = "Append-only by application policy, with traceable corrections."
NATURE = ("A structured self-reported activity record — what was said, in the "
          "person's own words, with only the times they chose to give.")


def display_timestamp(value: Any, language: str = "zh-Hant",
                      timezone_name: str = "Asia/Tokyo") -> str:
    """Format an absolute stored timestamp for a human reading surface.

    Stored ISO values remain untouched in Firestore, CSV and JSON. Their
    explicit offsets make them absolute instants even when older rows use JST.
    The records page and PDF may therefore convert them safely to the
    workspace's chosen IANA zone without migrating append-only history or
    changing the write contract.

    Invalid legacy values are returned verbatim instead of making an export
    fail. A bad timezone preference falls back to the offset already carried
    by the timestamp.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if stamp.tzinfo is None:
        return raw
    try:
        stamp = stamp.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        pass
    zone = stamp.tzname() or stamp.strftime("%z")
    if language == "en":
        return f"{stamp:%d %b %Y, %H:%M} {zone}".strip()
    return f"{stamp:%Y-%m-%d %H:%M} {zone}".strip()


def by_capture(rows: list[dict]) -> list[dict]:
    """
    One utterance, then the events filed from it.

    🔴 What a person reads back has to be what they said.

    The record page and the PDF both printed `source_text`, which is the span
    one event rests on — not the sentence. So "我覺得很累 哄睡了兩小時" came
    back, days later, as "哄睡了兩小時": the tiredness was in the ledger the
    whole time, in `transcript`, and simply never shown. V1 does not file a
    feeling as its own record, and that is a deliberate limit; showing a person
    two thirds of their own sentence is not a limit, it is a loss.

    So the whole utterance is printed once, and the events sit underneath it
    with their spans. `source_text` keeps its job as the event-level trace, and
    the CSV keeps both columns.

    Grouping is by capture, falling back to the transcript for rows written
    before captures were recorded on them.
    """
    groups: list[dict] = []
    index: dict[tuple, dict] = {}
    for r in rows:
        said = (r.get("transcript") or r.get("source_text") or "").strip()
        key = (r.get("capture_id") or "", said)
        group = index.get(key)
        if group is None:
            # `capture_id` is also the unit a person may withdraw. One spoken
            # sentence can become several event rows; exposing row-level
            # removal would let somebody keep half a sentence Mallow split and
            # accidentally discard the other half. The record ids travel only
            # to the authenticated discard endpoint so it can include a
            # restored active row that an older immutable receipt does not
            # know about.
            group = {"capture_id": r.get("capture_id") or "",
                     "transcript": said, "record_ids": [], "events": []}
            index[key] = group
            groups.append(group)
        if r.get("record_id"):
            group["record_ids"].append(r["record_id"])
        group["events"].append(r)
    return groups


def esc(value: Any) -> str:
    """
    Escape for ReportLab, which reads paragraph text as a small HTML dialect.

    Applied to every field, not just the person's own sentence: an activity
    label written by a model, or a version string from configuration, would
    break the document just as effectively.
    """
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# A cell that a spreadsheet would execute rather than display. Excel, Numbers
# and Sheets all treat these leading characters as the start of a formula, so a
# sentence beginning with one is prefixed to keep it text. The person's words
# are preserved exactly; only how a spreadsheet reads them changes.
FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def defuse(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in FORMULA_LEAD:
        return "'" + value
    return value


def to_csv(rows: Iterable[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: defuse(v) for k, v in r.items()})
    return buf.getvalue()


def to_json(rows: list[dict], *, demo: bool) -> dict[str, Any]:
    return {"records": rows, "storage_claim": CLAIM, "record_nature": NATURE,
            "audio_persisted": False, "demo_data": demo}


# ------------------------------------------------------------------- pdf ----
# Candidate fonts, in the order they are tried. A subset of a real TrueType
# face is embedded in the file, so the document renders the same wherever it is
# opened.
#
# The first attempt used ReportLab's built-in `STSong-Light` CID font, which
# embeds nothing and asks the reader to supply Adobe-GB1. Rendering the output
# showed what that costs: a reader without the language pack draws no Chinese at
# all, and GB1 is a Simplified mapping being asked to carry Traditional text. A
# person's own words are the whole point of this document; they should not
# depend on what the recipient happens to have installed.
CJK_FONTS = (
    ("WenQuanYiZenHei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ("NotoSansCJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ("NotoSansTC", "/usr/share/fonts/truetype/noto/NotoSansTC-Regular.ttf"),
    ("PingFang", "/System/Library/Fonts/PingFang.ttc"),
    # Local macOS development. These are never copied into the container; the
    # Docker image uses the first entry above.  Keeping real system fallbacks
    # means `./run.sh demo` can still export a PDF before deployment.
    ("STHeitiLight", "/System/Library/Fonts/STHeiti Light.ttc"),
    ("ArialUnicode", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
)

# Both alphabets, in one face. A record is Chinese sentences next to English
# activity labels, timestamps and digits, and ReportLab draws a paragraph in a
# single font: a face missing either half silently drops that half.
#
# This is not hypothetical. The first choice here was DroidSansFallback, which
# rendered the Chinese beautifully and dropped every digit and every English
# word on the page — it is a fallback face and carries no Latin at all. The
# check below is why that cannot happen again.
REQUIRED_GLYPHS = "A3、漢繁蘿"


class FontMissing(RuntimeError):
    """No usable face is installed, so the export would silently lose text."""


def covers(font, sample: str = REQUIRED_GLYPHS) -> bool:
    table = getattr(font.face, "charToGlyph", {})
    return all(table.get(ord(ch), 0) for ch in sample)


def register_cjk_font() -> str:
    """
    Find a font that can draw everything on the page, and embed a subset of it.

    Raises rather than settling for a partial face: a PDF with half its text
    missing is worse than an error, because it looks like it worked.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, path in CJK_FONTS:
        if name in pdfmetrics.getRegisteredFontNames():
            return name
        if not os.path.exists(path):
            continue
        try:
            font = TTFont(name, path)
        except Exception:                                         # noqa: BLE001
            continue
        if not covers(font):
            continue                      # draws one alphabet, drops the other
        pdfmetrics.registerFont(font)
        return name
    raise FontMissing(
        "no font covering both Latin and Chinese was found; "
        "install fonts-wqy-zenhei (see Dockerfile)")


def to_pdf(rows: list[dict], *, lang: str = "zh-Hant",
           timezone_name: str = "Asia/Tokyo",
           title: Optional[str] = None) -> bytes:
    """
    A quiet, readable document.

    No logo, no score, no chart, no total. Each entry is the person's own
    sentence, what kind of work Mallow filed it as, and whether the duration
    came from them or was simply absent. The document identifies itself as a
    structured self-reported activity record for personal reflection and
    optional sharing.

    The furniture follows the reader's language. The person's own sentence
    never does: `source_text` is printed exactly as it was said, in whatever
    language it was said in, on both versions of this document. Translating
    somebody's words inside a record they might hand to a professional would
    make the record about Mallow's reading rather than about them.
    """
    import i18n
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer)

    def s(key, **fmt):
        return i18n.t(key, lang, **fmt)

    title = title or s("pdf_title")
    cjk = register_cjk_font()
    base = getSampleStyleSheet()

    H = ParagraphStyle("H", parent=base["Title"], fontName=cjk, fontSize=15,
                       leading=20, spaceAfter=2)
    SUB = ParagraphStyle("SUB", parent=base["Normal"], fontName=cjk, fontSize=8.5,
                         leading=12, textColor="#6d6a5f", spaceAfter=14)
    SAID = ParagraphStyle("SAID", parent=base["Normal"], fontName=cjk, fontSize=11,
                          leading=16, spaceAfter=2)
    META = ParagraphStyle("META", parent=base["Normal"], fontName=cjk, fontSize=8.5,
                          leading=12, textColor="#6d6a5f", spaceAfter=10)
    FOOT = ParagraphStyle("FOOT", parent=base["Normal"], fontName=cjk, fontSize=8,
                          leading=11.5, textColor="#6d6a5f")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm)

    # Words, not emoji: STSong-Light is a CID font and carries no pictographs,
    # so a leaf or carrot glyph would render as a blank box on some readers and
    # vanish on others.
    food = {k: s("pdf_food_" + k) for k in ("grass", "carrot", "none", "withheld")}

    story: list[Any] = [Paragraph(title, H), Paragraph(s("pdf_sub"), SUB)]

    if not rows:
        story.append(Paragraph(s("pdf_empty"), SAID))

    # 🔴 The whole sentence once, then what was filed from it.
    #
    # This printed `source_text` per row — the span an event rests on, not the
    # utterance — so a PDF of "我覺得很累 哄睡了兩小時" said only "哄睡了兩小時".
    # The tiredness was in the export's own `transcript` column the whole time.
    # A document a person might hand to someone else has to be their sentence.
    for group in by_capture(rows):
        block: list[Any] = [Paragraph(f"「{esc(group['transcript'])}」", SAID)]
        many = len(group["events"]) > 1
        for r in group["events"]:
            # Everything here is escaped, not only the person's sentence: the
            # activity label and the version strings come from a model or from
            # configuration, and ReportLab reads Paragraph text as mini-HTML.
            bits = []
            span = (r.get("source_text") or "").strip()
            if span and (many or span != group["transcript"]):
                bits.append(f"「{esc(span)}」")
            bits += [food.get(r.get("policy_result"), "—"), esc(r.get("activity_text"))]
            occurred = (r.get("occurred_at") or "").strip()
            if occurred:
                clock = canonical_clock(occurred)
                bits.append(s("pdf_occurred_time", time=esc(clock)) if clock else
                            s("pdf_time_description", time=esc(occurred)))
            bits.append(s("pdf_minutes", n=esc(r["duration_minutes"]))
                        if r.get("duration_minutes") else s("pdf_no_time"))
            bits.append(s("pdf_recorded_time", time=esc(display_timestamp(
                r.get("recorded_at"), lang, timezone_name))))
            bits.append(esc(r.get("provenance")))
            if r.get("review_status") not in (None, "active"):
                bits.append(esc(r["review_status"]))
            block.append(Paragraph(" · ".join(b for b in bits if b), META))
        story.append(KeepTogether(block))

    story += [Spacer(1, 8 * mm),
              Paragraph(NATURE, FOOT),
              Paragraph(CLAIM, FOOT),
              Paragraph("Raw audio is processed in memory and is not persisted by Mallow.",
                        FOOT)]
    doc.build(story)
    return buf.getvalue()
