"""
iramuteq_parser.py

Reads a real Iramuteq analysis directly from its underlying files, rewritten
against an actual exported Iramuteq(14) project rather than assumed formats:

  <alceste folder>/profiles.csv   Iramuteq's own per-class specificity report
                                   (section markers + real word/chi2/p-value rows)
  <alceste folder>/uce.csv        UCE (segment) -> class number, 0 = unclassified
  <corpus folder>/corpus.db       'etoiles' table = document metadata,
                                   'luces' table  = segment -> document link
  <corpus folder>/uces.db         'uces' table = the actual segment text

<corpus folder> is assumed to be the parent directory of <alceste folder> -
this matches Iramuteq's own project layout, e.g.:
    my_corpus_1/
        corpus.db, uces.db, formes.db
        my_corpus_1_alceste_1/
            profiles.csv, uce.csv, afc_row.csv, ...

If your Iramuteq version names these differently, the loaders raise a clear
error naming the exact file/column that was expected, rather than silently
producing wrong data.
"""

from __future__ import annotations
import csv
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Generic, encoding-tolerant CSV reading (Iramuteq exports use ";" and ",")
# ---------------------------------------------------------------------------

def _read_csv_rows(path: Path) -> list[dict]:
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        for sep in (";", ","):
            try:
                with open(path, encoding=encoding, newline="") as f:
                    reader = csv.DictReader(f, delimiter=sep)
                    rows = list(reader)
                if rows and len(rows[0].keys()) > 1:
                    return rows
            except (UnicodeDecodeError, csv.Error) as e:
                last_error = e
                continue
    raise ValueError(f"Could not parse {path} with any known encoding/separator") from last_error


def _to_float(value) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    return float(str(value).strip().replace(",", "."))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class WordPoint:
    label: str
    class_id: str
    freq: int = 0
    chi2: float = 0.0
    pval: float = 1.0


@dataclass
class ClassPoint:
    class_id: str
    label: str
    n_segments: int = 0


@dataclass
class Segment:
    id: int
    class_id: str | None
    text: str
    variables: dict = field(default_factory=dict)


@dataclass
class AFCResult:
    axis1_pct: float
    axis2_pct: float
    total_classified: int
    words: list
    classes: list
    segments: list


# ---------------------------------------------------------------------------
# profiles.csv - Iramuteq's own per-class specificity report
# ---------------------------------------------------------------------------

def load_profiles(profiles_path: Path):
    """
    Parses the report-style structure actually used by Iramuteq's profiles.csv:
      "***";"nb classes";"9";...                          -> total class count
      "**";"classe";"1";...                                -> start of class 1
      "****";<class_size>;<total_classified>;<pct>;...     -> class summary
      <freq_in_class>;<total_freq>;<pct>;<chi2>;<word>;<pval>  -> word row

    Returns (classes: list[ClassPoint], words: list[WordPoint], total_classified: int).
    """
    classes = []
    words = []
    total_classified = 0
    current_class = None

    with open(profiles_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";", quotechar='"')
        next(reader, None)  # header row (generic V1..V6 labels, not useful)
        for row in reader:
            if not row:
                continue
            marker = row[0].strip()
            if marker == "***":
                continue  # global "nb classes" line - informational only
            if marker == "**":
                current_class = row[2].strip()
                continue
            if marker == "****":
                n_segments = int(row[1].strip())
                total_classified = int(row[2].strip())
                classes.append(ClassPoint(class_id=current_class, label=f"Classe {current_class}", n_segments=n_segments))
                continue
            if current_class is None or len(row) < 6:
                continue
            try:
                freq_in_class = int(row[0].strip())
                chi2 = _to_float(row[3])
                word = row[4].strip()
                pval = _to_float(row[5])
            except (ValueError, IndexError):
                continue
            words.append(WordPoint(label=word, class_id=current_class, freq=freq_in_class, chi2=chi2, pval=pval))

    return classes, words, total_classified


# ---------------------------------------------------------------------------
# uce.csv - the real UCE (segment) -> class number mapping
# ---------------------------------------------------------------------------

def load_uce_class_map(uce_csv_path: Path) -> dict:
    """
    Returns {uce_id (0-indexed): class_id str, or None if unclassified (value 0)}.
    uce.csv's row label is 1-indexed (row "1" == uce id 0).
    """
    rows = _read_csv_rows(uce_csv_path)
    label_col = list(rows[0].keys())[0]
    class_col = list(rows[0].keys())[1]
    mapping = {}
    for r in rows:
        uce_1indexed = int(r[label_col])
        class_num = r[class_col].strip()
        mapping[uce_1indexed - 1] = None if class_num == "0" else class_num
    return mapping


# ---------------------------------------------------------------------------
# corpus.db / uces.db - the actual segment text + document metadata
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\*\s*(\w+?)_(\S+)")


def load_segments_from_db(corpus_dir: Path, uce_class_map: dict) -> list:
    """
    Joins corpus.db (document metadata + which segment belongs to which
    document) with uces.db (the segment's actual text) and the class mapping
    above, into one flat list of Segment objects. Unclassified segments
    (Iramuteq's "class 0") are skipped, matching the "% classified" figure
    Iramuteq itself reports in info.txt.
    """
    corpus_db = corpus_dir / "corpus.db"
    uces_db = corpus_dir / "uces.db"
    if not corpus_db.exists() or not uces_db.exists():
        raise FileNotFoundError(
            f"Expected corpus.db and uces.db in {corpus_dir} (the corpus folder, "
            f"normally one level above the analysis folder) - not found there."
        )

    con = sqlite3.connect(str(corpus_db))
    cur = con.cursor()
    cur.execute("SELECT uci, et FROM etoiles")
    et_by_uci = dict(cur.fetchall())
    cur.execute("SELECT uce, uci FROM luces")
    uci_by_uce = dict(cur.fetchall())
    con.close()

    con = sqlite3.connect(str(uces_db))
    cur = con.cursor()
    cur.execute("SELECT id, uces FROM uces")
    text_by_uce = dict(cur.fetchall())
    con.close()

    segments = []
    for uce_id, text in text_by_uce.items():
        class_id = uce_class_map.get(uce_id)
        if class_id is None:
            continue
        uci = uci_by_uce.get(uce_id)
        et = et_by_uci.get(uci, "")
        variables = {m.group(1): m.group(2) for m in _VAR_RE.finditer(et)}
        segments.append(Segment(id=uce_id, class_id=class_id, text=text, variables=variables))

    segments.sort(key=lambda s: s.id)
    return segments


# ---------------------------------------------------------------------------
# AFC eigenvalues (optional - only needed for a future factorial-plane view,
# not required for the coverage timeline)
# ---------------------------------------------------------------------------

def load_afc_factors(path: Path):
    rows = _read_csv_rows(path)
    pct_col = next(c for c in rows[0].keys() if "pourcent" in c.lower() or "percent" in c.lower())
    pct_values = [_to_float(r[pct_col]) for r in rows]
    axis1 = pct_values[0] if len(pct_values) > 0 else 0.0
    axis2 = pct_values[1] if len(pct_values) > 1 else 0.0
    return axis1, axis2


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_afc_result(analysis_dir: str, corpus_dir: str | None = None) -> AFCResult:
    """
    analysis_dir: the alceste_N classification folder (profiles.csv, uce.csv, ...)
    corpus_dir:   the corpus folder (corpus.db, uces.db). Defaults to
                  analysis_dir's parent, matching Iramuteq's own layout.
    """
    analysis_dir = Path(analysis_dir)
    corpus_dir = Path(corpus_dir) if corpus_dir else analysis_dir.parent

    profiles_path = analysis_dir / "profiles.csv"
    uce_path = analysis_dir / "uce.csv"
    if not profiles_path.exists():
        raise FileNotFoundError(f"profiles.csv not found in {analysis_dir}")
    if not uce_path.exists():
        raise FileNotFoundError(f"uce.csv not found in {analysis_dir}")

    classes, words, total_classified = load_profiles(profiles_path)
    uce_class_map = load_uce_class_map(uce_path)
    segments = load_segments_from_db(corpus_dir, uce_class_map)

    axis1_pct = axis2_pct = 0.0
    afc_facteur_path = analysis_dir / "afc_facteur.csv"
    if afc_facteur_path.exists():
        try:
            axis1_pct, axis2_pct = load_afc_factors(afc_facteur_path)
        except Exception:
            pass  # optional - a missing/odd afc_facteur.csv shouldn't block the timeline

    return AFCResult(
        axis1_pct=axis1_pct,
        axis2_pct=axis2_pct,
        total_classified=total_classified,
        words=words,
        classes=classes,
        segments=segments,
    )
