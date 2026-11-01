"""Where the journal lives on your PC: one trades file and a folder of screenshots, in the trader folder.

    trader/journal/trades.json          the trades (the page saves the whole list)
    trader/journal/trades.prev.json     the version before the last save, in case a save goes wrong
    trader/journal/images/<id>.png      screenshots, named by a fingerprint of their contents

Standard library only. Nothing here talks to the network.
"""

import hashlib
import json
import os
import re

MAX_TRADES = 20000
MAX_IMAGE = 15 * 1024 * 1024
IMAGE_NAME = re.compile(r"^[0-9a-f]{16}\.(png|jpg|webp|gif)$")


class StoreError(Exception):
    pass


def journal_dir(data_dir):
    return os.path.join(data_dir, "journal")


def image_dir(data_dir):
    return os.path.join(journal_dir(data_dir), "images")


def load_trades(data_dir):
    """The saved trades, or None when nothing has been saved yet."""
    path = os.path.join(journal_dir(data_dir), "trades.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        raise StoreError("the saved journal could not be read; the previous copy is trades.prev.json in the journal folder")
    trades = doc.get("trades") if isinstance(doc, dict) else doc
    if not isinstance(trades, list):
        raise StoreError("the saved journal has an unexpected shape")
    return [t for t in trades if isinstance(t, dict)]


def save_trades(data_dir, trades):
    if not isinstance(trades, list) or len(trades) > MAX_TRADES or not all(isinstance(t, dict) for t in trades):
        raise StoreError("the journal must be a list of at most %d trades" % MAX_TRADES)
    folder = journal_dir(data_dir)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "trades.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": 2, "trades": trades}, fh, ensure_ascii=False)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as src, open(os.path.join(folder, "trades.prev.json"), "wb") as dst:
                dst.write(src.read())
        except OSError:
            pass
    os.replace(tmp, path)
    return len(trades)


def image_kind(data):
    """png, jpg, webp or gif by the file's own first bytes, never by what the sender says it is."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"GIF8":
        return "gif"
    return None


def save_image(data_dir, data):
    """Keep a screenshot and return its name. The same picture saved twice is stored once."""
    if not data or len(data) > MAX_IMAGE:
        raise StoreError("the image is empty or larger than %d MB" % (MAX_IMAGE // 1024 // 1024))
    kind = image_kind(data)
    if not kind:
        raise StoreError("that is not a PNG, JPEG, WebP or GIF image")
    name = "%s.%s" % (hashlib.sha1(data).hexdigest()[:16], kind)
    folder = image_dir(data_dir)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        with open(path, "wb") as fh:
            fh.write(data)
    return name


def read_image(data_dir, name):
    """(bytes, type) for a stored screenshot, or None. The name must be one this module made."""
    if not IMAGE_NAME.match(name or ""):
        return None
    try:
        with open(os.path.join(image_dir(data_dir), name), "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    ext = name.rsplit(".", 1)[1]
    return data, {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}[ext]
