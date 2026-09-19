"""
server.py

Local web-app: a small Flask server that serves the page and answers its
requests over ordinary HTTP (see earlier notes on why: avoids the
GTK/Qt/WebKit version conflicts pywebview hit on this machine).

Run:   python3 server.py
Then:  opens http://127.0.0.1:5050 in your default browser automatically.

Requires: pip3 install --user flask
"""

import csv
import difflib
import json
import re
import threading
import time
import webbrowser
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from iramuteq_parser import build_afc_result
from iramuteq_timeline_data import assign_colors, derive_period_fields

APP_DIR = Path(__file__).parent
DATE_VARIABLE = "date"
MONTH_VARIABLE = "yearmonth"
YEAR_VARIABLE = "year"
EVENTS_FILENAME = "timeline_events.json"
CLASS_NAMES_FILENAME = "timeline_class_names.json"
PORT = 5050

app = Flask(__name__)


def _clean(s):
    return s.strip().strip("'\"") if s else s


def _parse_csv_rows(csv_path: str):
    """Encoding/separator/quoting-tolerant CSV read, shared by every CSV feature."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        for sep in (",", ";", "\t"):
            for quotechar in ('"', "'"):
                try:
                    with open(csv_path, encoding=encoding, newline="") as f:
                        reader = csv.DictReader(f, delimiter=sep, quotechar=quotechar)
                        candidate = list(reader)
                    if candidate and len(candidate[0].keys()) > 1:
                        return candidate
                except (UnicodeDecodeError, csv.Error):
                    continue
    raise ValueError("could not parse this file as CSV with any common encoding/separator/quoting style")


def _load_csv_metadata(csv_path: str):
    """
    Optional feature: reads the original source CSV (the one the corpus was
    built from) and returns a rich per-row record, not just a URL:
        {rawnb_value: {"url", "title", "outlet", "publish_date"}}

    Column detection:
    - "rawnb": an explicit column containing "raw" and "nb", else falls back
      to the row's position counting the header as row 1 (Excel's own row
      numbers) - the first data row is row 2.
    - "url": exact match, else a whole-word match (so "media_url" is never
      mistaken for it).
    - "title", "outlet" (media_name/outlet/source/publication), "publish_date"
      (publish_date/date/published): used only if present - each is optional,
      and features that need one simply do less when it's missing.
    """
    rows = _parse_csv_rows(csv_path)
    headers = list(rows[0].keys())
    cleaned_headers = {h: _clean(h).lower() for h in headers}

    def find_col(*exact_names, contains_all=None):
        for h in headers:
            if cleaned_headers[h] in exact_names:
                return h
        if contains_all:
            for h in headers:
                if all(tok in cleaned_headers[h] for tok in contains_all):
                    return h
        return None

    url_col = find_col("url")
    if url_col is None:
        url_col = next((h for h in headers if re.search(r"\burl\b", cleaned_headers[h])), None)
    if url_col is None:
        raise ValueError(f"no 'url' column found - columns present: {headers}")

    rawnb_col = find_col(contains_all=("raw", "nb"))
    title_col = find_col("title")
    if title_col is None:
        title_col = next((h for h in headers if "title" in cleaned_headers[h]), None)

    outlet_col = find_col("media_name", "outlet", "source", "publication")
    if outlet_col is None:
        outlet_col = next((h for h in headers if "outlet" in cleaned_headers[h]
                            or ("media" in cleaned_headers[h] and "name" in cleaned_headers[h])), None)

    date_col = find_col("publish_date", "date", "published")
    if date_col is None:
        date_col = next((h for h in headers if "publish" in cleaned_headers[h] and "date" in cleaned_headers[h]), None)
    if date_col is None:
        date_col = next((h for h in headers if "date" in cleaned_headers[h]), None)

    indexed_col = find_col("indexed_date")
    if indexed_col is None:
        indexed_col = next((h for h in headers if "indexed" in cleaned_headers[h] and "date" in cleaned_headers[h]), None)

    mapping = {}
    skipped_no_url = 0
    for i, row in enumerate(rows):
        key = _clean(row[rawnb_col]) if rawnb_col else str(i + 2)
        url = _clean(row.get(url_col) or "")
        if url and not url.lower().startswith(("http://", "https://")):
            url = None  # safety net: never store a non-URL, even if column-matching mis-fires
        if not url:
            skipped_no_url += 1
            continue
        if key:
            mapping[key] = {
                "url": url,
                "title": _clean(row.get(title_col) or "") if title_col else None,
                "outlet": _clean(row.get(outlet_col) or "") if outlet_col else None,
                "publish_date": _clean(row.get(date_col) or "") if date_col else None,
                "indexed_date": _clean(row.get(indexed_col) or "") if indexed_col else None,
            }

    meta = {
        "used_explicit_rawnb_col": rawnb_col is not None,
        "has_title": title_col is not None,
        "has_outlet": outlet_col is not None,
        "has_date": date_col is not None,
        "total_rows": len(rows),
        "skipped_no_url": skipped_no_url,
    }
    return mapping, meta


def _detect_similar_titles(csv_meta_by_rawnb: dict, rawnb_values: set, similarity_threshold: float = 0.75, min_outlets: int = 2, max_per_day: int = 400):
    """
    Groups the corpus's own documents (by rawnb) by publish day, then finds
    clusters of near-identical titles published by different outlets the
    same day - a simple, transparent proxy for syndicated/wire-copy
    coverage. This is a lead worth checking, not a certainty: it only
    compares titles (not full article bodies), so paraphrased rewrites of
    the same wire story can be missed, and coincidentally similar short
    titles on unrelated stories could rarely be flagged.
    """
    by_date = defaultdict(list)
    for rawnb in rawnb_values:
        meta = csv_meta_by_rawnb.get(rawnb)
        if not meta or not meta.get("title") or not meta.get("publish_date"):
            continue
        day = meta["publish_date"][:10]
        by_date[day].append({"rawnb": rawnb, **meta})

    def norm(t):
        return re.sub(r"[^\w\s]", "", t.lower()).strip()

    clusters = []
    skipped_days = 0
    for day, items in by_date.items():
        if len(items) > max_per_day:
            skipped_days += 1
            continue
        used = [False] * len(items)
        for i in range(len(items)):
            if used[i]:
                continue
            ti = norm(items[i]["title"])
            if not ti:
                continue
            group = [items[i]]
            used[i] = True
            for j in range(i + 1, len(items)):
                if used[j]:
                    continue
                tj = norm(items[j]["title"])
                if not tj:
                    continue
                if difflib.SequenceMatcher(None, ti, tj).ratio() >= similarity_threshold:
                    group.append(items[j])
                    used[j] = True
            outlets = set(g.get("outlet") for g in group if g.get("outlet"))
            if len(group) >= 2 and len(outlets) >= min_outlets:
                group.sort(key=lambda g: g.get("indexed_date") or "")
                clusters.append({"date": day, "items": group})

    clusters.sort(key=lambda c: len(set(g.get("outlet") for g in c["items"])), reverse=True)
    return clusters, skipped_days


# --- small JSON-file-backed stores, kept next to the analysis folder so ---
# --- they persist across runs, same pattern for events and class names ---

def _json_path(folder_path: str, filename: str) -> Path:
    return Path(folder_path) / filename


def _load_json(folder_path: str, filename: str, default):
    path = _json_path(folder_path, filename)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _save_json(folder_path: str, filename: str, data) -> None:
    _json_path(folder_path, filename).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_events(folder_path: str) -> list:
    """Loads events, migrating older saved files (no id/class_id) in place."""
    events = _load_json(folder_path, EVENTS_FILENAME, [])
    changed = False
    next_id = 1
    for e in events:
        if "id" not in e:
            e["id"] = next_id
            changed = True
        next_id = max(next_id, e["id"] + 1)
        if "class_id" not in e:
            e["class_id"] = None
            changed = True
    if changed:
        _save_json(folder_path, EVENTS_FILENAME, events)
    return events


@app.route("/")
def index():
    response = send_from_directory(APP_DIR, "timeline_app.html")
    # avoid the browser silently serving a stale cached copy after an update
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/api/load", methods=["POST"])
def api_load():
    data = request.get_json(force=True)
    folder_path = (data.get("folder") or "").strip()
    csv_path = (data.get("csv_path") or "").strip()

    if not folder_path:
        return jsonify({"error": "Please provide the analysis folder path."})
    if not Path(folder_path).is_dir():
        return jsonify({"error": f"'{folder_path}' is not a folder that exists."})

    csv_meta_by_rawnb = {}
    csv_note = None
    if csv_path:
        if not Path(csv_path).is_file():
            return jsonify({"error": f"'{csv_path}' is not a file that exists."})
        try:
            csv_meta_by_rawnb, m = _load_csv_metadata(csv_path)
            extras = []
            if not m["has_title"]:
                extras.append("no title column found")
            if not m["has_outlet"]:
                extras.append("no outlet column found")
            if not m["has_date"]:
                extras.append("no publish-date column found")
            csv_note = (
                f"Linked {len(csv_meta_by_rawnb)} of {m['total_rows']} CSV row(s)"
                + (f" ({m['skipped_no_url']} had no usable URL)" if m["skipped_no_url"] else "")
                + (" (matched via a rawnb-like column)." if m["used_explicit_rawnb_col"]
                   else " (matched by row position - worth checking one #rawnb link against its known article to confirm the alignment).")
                + (f" Note: {', '.join(extras)}." if extras else "")
            )
        except Exception as e:
            return jsonify({"error": f"Could not use that CSV: {e}"})

    try:
        result = build_afc_result(folder_path)
    except Exception as e:
        return jsonify({"error": str(e)})

    colors = assign_colors(result.classes)
    period_fields, availability = derive_period_fields(
        result.segments, date_variable=DATE_VARIABLE,
        month_variable=MONTH_VARIABLE, year_variable=YEAR_VARIABLE,
    )
    period_by_id = {p["id"]: p for p in period_fields}

    class_names = _load_json(folder_path, CLASS_NAMES_FILENAME, {})
    classes_out = []
    for c in result.classes:
        classes_out.append({
            "id": c.class_id,
            "default_label": c.label,
            "label": class_names.get(c.class_id, c.label),
            "n_segments": c.n_segments,
            "hex": colors[c.class_id],
        })

    words_by_class = {}
    for w in result.words:
        words_by_class.setdefault(w.class_id, []).append({"word": w.label, "chi2": w.chi2, "pval": w.pval})

    segments_out = []
    for seg in result.segments:
        p = period_by_id.get(seg.id, {})
        rawnb_val = seg.variables.get("rawnb")
        csv_meta = csv_meta_by_rawnb.get(rawnb_val) if rawnb_val else None
        segments_out.append({
            "id": seg.id,
            "class_id": seg.class_id,
            "source": seg.variables.get("source") or seg.variables.get("outlet"),
            "type": seg.variables.get("type"),
            "rawnb": rawnb_val,
            "url": csv_meta["url"] if csv_meta else None,
            "title": csv_meta.get("title") if csv_meta else None,
            "outlet": (csv_meta.get("outlet") if csv_meta else None),
            "publish_date": csv_meta.get("publish_date") if csv_meta else None,
            "indexed_date": csv_meta.get("indexed_date") if csv_meta else None,
            "text": seg.text,
            "day": p.get("day"),
            "month": p.get("month"),
            "year": p.get("year"),
        })

    similar_title_clusters = []
    if csv_meta_by_rawnb:
        rawnb_values_in_corpus = {seg.variables.get("rawnb") for seg in result.segments if seg.variables.get("rawnb")}
        similar_title_clusters, _skipped_days = _detect_similar_titles(csv_meta_by_rawnb, rawnb_values_in_corpus)

    return jsonify({
        "classes": classes_out,
        "words_by_class": words_by_class,
        "segments": segments_out,
        "availability": availability,
        "total_classified": result.total_classified,
        "events": _load_events(folder_path),
        "csv_note": csv_note,
        "similar_title_clusters": similar_title_clusters[:50],
    })


@app.route("/api/rename_class", methods=["POST"])
def api_rename_class():
    data = request.get_json(force=True)
    folder_path, class_id, name = data["folder"], data["class_id"], data["name"]
    names = _load_json(folder_path, CLASS_NAMES_FILENAME, {})
    if name:
        names[class_id] = name
    else:
        names.pop(class_id, None)  # empty name = reset to default label
    _save_json(folder_path, CLASS_NAMES_FILENAME, names)
    return jsonify(names)


@app.route("/api/add_event", methods=["POST"])
def api_add_event():
    data = request.get_json(force=True)
    folder_path = data["folder"]
    events = _load_events(folder_path)
    next_id = max([e["id"] for e in events], default=0) + 1
    events.append({
        "id": next_id,
        "granularity": data["granularity"],
        "period": data["period"],
        "class_id": data.get("class_id"),
        "title": data["title"],
        "note": data.get("note", ""),
    })
    events.sort(key=lambda e: (e["granularity"], e["period"]))
    _save_json(folder_path, EVENTS_FILENAME, events)
    return jsonify(events)


@app.route("/api/remove_event", methods=["POST"])
def api_remove_event():
    data = request.get_json(force=True)
    folder_path = data["folder"]
    events = [e for e in _load_events(folder_path) if e["id"] != data["event_id"]]
    _save_json(folder_path, EVENTS_FILENAME, events)
    return jsonify(events)


def _open_browser():
    time.sleep(1)
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    except Exception:
        pass  # no GUI browser available (e.g. running inside a container) - harmless


if __name__ == "__main__":
    threading.Timer(1, _open_browser).start()
    # host="0.0.0.0" so the server accepts connections from outside the
    # container - Flask's default (127.0.0.1) would only be reachable from
    # inside the container itself, making port mapping useless.
    app.run(host="0.0.0.0", port=PORT, debug=False)
