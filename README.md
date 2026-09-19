# Interactive Iramuteq corpus exploration

A local web app that sits on top of an Iramuteq (Reinert-method) text
classification and makes it explorable: a coverage timeline by class,
real article/outlet data joined in from a Media Cloud CSV, an "Outlets ×
classes" bias check, wire-copy/syndicated-coverage detection, a searchable
corpus, and a Dossier for collecting sourced citations (with a Zotero
export).

It runs entirely on your machine. Nothing is sent anywhere else.

## What it needs from you

1. An Iramuteq project folder, produced by Iramuteq itself, shaped like:
   ```
   my_corpus_1/
     corpus.db, uces.db, formes.db          <- the corpus itself
     my_corpus_1_alceste_1/                  <- one classification run
       profiles.csv, uce.csv, afc_row.csv, afc_col.csv, afc_facteur.csv, ...
   ```
   You point the app at the **alceste_N folder** (the classification run);
   it automatically looks one level up for `corpus.db`/`uces.db`.

2. *(Optional)* The **Media Cloud CSV** the corpus was actually built from
   — this is what unlocks real article titles, real outlet names (instead
   of Iramuteq's generic `*source_` tag), real dates, clickable links to
   the original articles, the outlet-bias check, and wire-copy detection.
   Without it, the app still works, just with less real-world context.

## Quick start (Docker)

```bash
docker build -t iramuteq-explorer .
docker run --rm -p 5050:5050 -v /path/to/your/iramuteq/projects:/data iramuteq-explorer
```

Then open **http://localhost:5050** yourself — the app tries to open your
browser automatically, but that trick doesn't work from inside a
container, so don't wait on it.

Or with Docker Compose (put your project folders in a `data/` folder next
to this README first):
```bash
docker compose up --build
```

### The one thing that trips people up: paths

The app runs *inside the container*, so it can't see your host machine's
real file paths — only whatever you've mounted into `/data`. If your real
folder is:
```
/home/you/projects/my_corpus_1/my_corpus_1_alceste_1
```
mount the parent:
```bash
-v /home/you/projects:/data
```
and in the app's "Analysis folder" field, type the **container path**,
not your real one:
```
/data/my_corpus_1/my_corpus_1_alceste_1
```
Same goes for the optional CSV field — it also needs to be somewhere
under the folder you mounted.

## Running without Docker

Just as valid, and skips the path-mapping step above entirely since
there's no container in the way:
```bash
pip3 install flask
python3 server.py
```
Then use your real, ordinary filesystem paths directly in the app.

## What gets saved, and where

Annotations you add (marked periods, class renames, Dossier tags/comments)
are written as small JSON files (`timeline_events.json`,
`timeline_class_names.json`) **inside the analysis folder you point the
app at**. The Dossier itself lives only in the browser tab's memory and
is lost on reload — use "Copy all as citations" or "Export for Zotero
(.ris)" before closing it if you want to keep it.

This means: as long as you mount the same folder next time, your
annotations and renamed classes are still there. Nothing is stored inside
the Docker image itself.

## Feature overview

- **Coverage Timeline**: stacked bar chart by class, switchable between
  Day / Month / Year (whichever your corpus's date metadata actually
  supports — the app is honest about which granularities are available
  rather than faking precision it doesn't have).
- **Zoom / drill-down**: double-click a class in a Year bar to zoom into
  that year's months; double-click again to zoom into a month's days.
- **Segments ranked by real statistics**: within any period, segments are
  sorted by the sum of χ² (Iramuteq's own specificity test, from
  `profiles.csv`) of the characteristic words actually found in them, with
  each word's p-value shown as a plain-language threshold
  (`p<0.0001`, etc.) — not just the raw number.
- **Search**: full-text search across the corpus, plus a direct check of
  whether the searched word/form is one of Iramuteq's own statistically
  characteristic words for any class.
- **Outlets × classes**: a foldable table (folded by default) showing
  each outlet's article/segment counts and an over/under-representation
  index per class, with a warning if one outlet dominates the corpus
  enough to skew the results.
- **Possible shared/syndicated coverage**: a foldable panel flagging
  near-identical headlines published by different outlets the same day —
  a lead worth checking, not a certainty.
- **Dossier**: collect segments with a click, add your own tags and
  comments, show tags as colored markers on the chart (color assignable
  per tag), filter the Dossier by tag, and export everything either as
  plain-text citations or as a `.ris` file Zotero can import directly
  (tags become real Zotero tags).
- **"How these statistics are calculated"**: a foldable panel (folded by
  default) spelling out exactly which numbers come from Iramuteq itself
  and which are computed by this app on top of it.

## Troubleshooting

- **Permission errors writing the JSON annotation files** (Linux/macOS,
  Docker only): add `--user $(id -u):$(id -g)` to the `docker run`
  command so the container writes as your own user instead of root.
- **"No such file or directory" for your analysis folder**: almost always
  the path-mapping issue above — check you're typing the `/data/...` path,
  not your real host path.
- **A stale-looking page after updating the code**: hard-refresh the
  browser tab (Ctrl+Shift+R) — Flask serves the page fresh every time, but
  browsers can still cache aggressively.

## Honest limitations

- Wire-copy detection compares **titles only**, not full article text, and
  skips any single day with more than 400 candidate articles to stay fast.
- The CSV-linking column detection (title/outlet/date/rawnb/url) uses
  header-name heuristics, not a fixed schema — it's been tested against
  real Media Cloud exports, but an unusual export format could still need
  a tweak.
- This is a **local, single-user** tool with no authentication. Don't
  expose port 5050 beyond your own machine.
