# Interactive Iramuteq corpus exploration
#
# Small, single-purpose image: Flask is the only real dependency (sqlite3,
# csv, difflib, etc. are all Python standard library - no compilers, no
# system packages, no GUI toolkit needed at all, unlike the earlier
# pywebview approach this app started from).

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir flask

COPY iramuteq_parser.py iramuteq_timeline_data.py server.py timeline_app.html ./

# Your Iramuteq project folders (and, optionally, the Media Cloud CSV) are
# expected to be mounted here at run time - see README.md. The app writes
# a couple of small JSON files (annotations, renamed classes) back into
# whatever folder you point it at, so mounting a real host directory here
# (rather than baking corpora into the image) is what makes that persist.
VOLUME ["/data"]

EXPOSE 5050

CMD ["python3", "server.py"]
