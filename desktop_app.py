"""
desktop_app.py

Launch with:  python desktop_app.py
Requires:     pip install pywebview
On Ubuntu, pywebview needs WebKitGTK, usually already present; if not:
              sudo apt install python3-gi gir1.2-webkit2-4.0

This is the glue: a native window showing timeline_app.html, with a small
Python API (exposed as window.pywebview.api in the page's JS) that does the
actual file picking and parsing. All the Iramuteq-specific logic stays in
iramuteq_parser.py / iramuteq_timeline_data.py - this file only wires it to
the UI.
"""

import json
from pathlib import Path

import webview

from iramuteq_parser import build_afc_result
from iramuteq_timeline_data import build_timeline_payload, _parse_date, _week_start

APP_DIR = Path(__file__).parent
SHELL_PATH = APP_DIR / "timeline_app.html"

# If the GTK/WebKit backend keeps crashing on this machine (a known issue on
# Ubuntu 20.04, which only has webkit2gtk-4.0), set this to "qt" instead -
# requires: sudo apt install python3-pyqt5 python3-pyqt5.qtwebengine
#           pip3 install --user pywebview[qt]
GUI_BACKEND = "qt"  # GTK's WebKit path is broken on this machine (webkit2gtk-4.0 only) - forcing Qt instead

# Change this if your corpus tags dates under a different variable name,
# e.g. *date_... vs *jour_... - whatever you used when building the corpus.
DATE_VARIABLE = "date"

# Events (your manually-added "what happened" markers) are saved next to the
# analysis folder itself, so they stay attached to that project across runs.
EVENTS_FILENAME = "timeline_events.json"


def _events_path(folder_path: str) -> Path:
    return Path(folder_path) / EVENTS_FILENAME


def _load_events(folder_path: str) -> list:
    path = _events_path(folder_path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _save_events(folder_path: str, events: list) -> None:
    _events_path(folder_path).write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class Api:
    def pick_folder(self):
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    def pick_corpus(self):
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Text files (*.txt)", "All files (*.*)"),
        )
        return result[0] if result else None

    def load(self, folder_path: str, corpus_path: str):
        try:
            result = build_afc_result(folder_path, corpus_path)
        except Exception as e:
            return {"error": str(e)}

        timeline = build_timeline_payload(result, date_variable=DATE_VARIABLE)

        # Flatten segments for the client-side drill-down: attach the week
        # each one falls into (or None, matching the skip logic above) plus
        # a couple of metadata fields worth showing on a card.
        segments_out = []
        for seg in result.segments:
            raw = seg.variables.get(DATE_VARIABLE)
            parsed = _parse_date(raw) if raw else None
            week = _week_start(parsed) if parsed else None
            segments_out.append({
                "class_id": seg.class_id,
                "week": week,
                "source": seg.variables.get("source") or seg.variables.get("outlet"),
                "type": seg.variables.get("type"),
                "text": seg.text,
            })

        return {
            "timeline": timeline,
            "segments": segments_out,
            "events": _load_events(folder_path),
        }

    def add_event(self, folder_path: str, week: str, title: str, note: str):
        events = _load_events(folder_path)
        events = [e for e in events if e["week"] != week]  # replace if same week
        events.append({"week": week, "title": title, "note": note})
        events.sort(key=lambda e: e["week"])
        _save_events(folder_path, events)
        return events

    def remove_event(self, folder_path: str, week: str):
        events = [e for e in _load_events(folder_path) if e["week"] != week]
        _save_events(folder_path, events)
        return events


if __name__ == "__main__":
    api = Api()
    webview.create_window(
        "Coverage Timeline",
        str(SHELL_PATH),
        js_api=api,
        width=1150,
        height=820,
    )
    # debug=True opens dev tools (right-click > Inspect) and prints JS errors
    # to this terminal - keep it on until the app is working reliably, it
    # costs nothing and is the fastest way to see what actually failed.
    webview.start(debug=True, gui=GUI_BACKEND)
