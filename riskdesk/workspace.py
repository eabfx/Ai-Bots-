"""Small saved notes the dashboard keeps for you: key levels, open trades, a monthly goal, end-of-day wrap-ups, the cooldown timer.

One file (trader/workspace.json) so every device sees the same ones and the daily backup carries them. The page decides what is inside
each note; this module only checks the name, the size, and that the file is written safely.
"""
import json
import os

FILE = "workspace.json"
KEYS = ("levels", "openrisk", "goal", "wrapups", "cooldown", "startday")
MAX_NOTE = 60000            # bytes of JSON per note
MAX_TOTAL = 400000


class WorkspaceError(Exception):
    pass


def _path(data_dir):
    return os.path.join(data_dir, FILE)


def load(data_dir):
    try:
        with open(_path(data_dir), encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in doc.items() if k in KEYS} if isinstance(doc, dict) else {}


def save(data_dir, key, value):
    """Replace one note. A value of null removes it. Returns the whole workspace."""
    if key not in KEYS:
        raise WorkspaceError("unknown note %r" % (key,))
    doc = load(data_dir)
    if value is None:
        doc.pop(key, None)
    else:
        try:
            text = json.dumps(value, separators=(",", ":"))
        except (TypeError, ValueError):
            raise WorkspaceError("that note cannot be saved")
        if len(text.encode("utf-8")) > MAX_NOTE:
            raise WorkspaceError("that note is too large")
        doc[key] = value
    if len(json.dumps(doc).encode("utf-8")) > MAX_TOTAL:
        raise WorkspaceError("the saved notes are too large")
    tmp = _path(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, _path(data_dir))
    return doc
