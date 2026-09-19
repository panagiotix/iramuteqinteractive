"""
iramuteq_timeline_data.py

Turns Segment objects into per-segment day/month/year period keys, and
assigns a stable color per class. Granularity switching and aggregation
itself now happen client-side (see timeline_app.html) so the person can
flip between day/month/year without a server round-trip - this module's
job is just to compute, once, which period each segment falls into at each
granularity, and to be honest about which granularities are actually usable
for a given corpus (many real Iramuteq exports only carry month/year
metadata, not a daily date).
"""

from __future__ import annotations
from datetime import datetime

PALETTE = ["#b2402f", "#2f6e63", "#b7862a", "#4a5a73", "#7a5c3e", "#6b4c9a", "#9b4f8c", "#5c8a3a", "#c77b3e"]

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]


def _parse_date(raw: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def assign_colors(classes) -> dict:
    return {c.class_id: PALETTE[i % len(PALETTE)] for i, c in enumerate(classes)}


def derive_period_fields(segments, date_variable="date", month_variable="yearmonth", year_variable="year"):
    """
    For each segment, compute day/month/year keys wherever derivable:
    prefers an actual date_variable (e.g. "2026-09-03") when present, and
    falls back to coarser variables (yearmonth "2026-09", year "2026") that
    many real Iramuteq corpora carry instead of a full date.

    Returns (fields: list[{"id", "day", "month", "year"}] aligned to
    segments, availability: {"day": bool, "month": bool, "year": bool}
    reporting whether at least one segment has a usable value at that
    granularity).
    """
    out = []
    avail = {"day": False, "month": False, "year": False}
    for seg in segments:
        raw_date = seg.variables.get(date_variable)
        parsed = _parse_date(raw_date) if raw_date else None
        if parsed:
            day = parsed.strftime("%Y-%m-%d")
            month = parsed.strftime("%Y-%m")
            year = parsed.strftime("%Y")
        else:
            day = None
            month = seg.variables.get(month_variable)
            year = seg.variables.get(year_variable) or (month.split("-")[0] if month else None)
        if day:
            avail["day"] = True
        if month:
            avail["month"] = True
        if year:
            avail["year"] = True
        out.append({"id": seg.id, "day": day, "month": month, "year": year})
    return out, avail
