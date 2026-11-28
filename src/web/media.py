"""Images in notes: editors upload them (drag and drop in the note editor),
everyone can see them.

    POST /studio/upload   (lecturers / admins, CSRF)  -> {"url": "/media/<name>", "markdown": "![alt](/media/<name>)"}
    GET  /media/<name>

Only PNG, JPEG, GIF and WebP, at most MAX_BYTES, recognised by their first
bytes (not by the file name). SVG is refused: it can carry scripts. Files are
stored under MEDIA_DIR (.env, default data/media) with a random name, and
served with nosniff so a browser never runs them as anything else.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory
from flask_babel import gettext as _

from src.web import deps
from src.web.security import login_required

bp = Blueprint("media", __name__)

MAX_BYTES = 5 * 1024 * 1024
SIGNATURES = [  # (first bytes, extension, mimetype)
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
]
MIMES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def media_dir() -> Path:
    path = Path(os.getenv("MEDIA_DIR", "data/media")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def sniff(head: bytes) -> str | None:
    """The image's real type from its first bytes, or None."""
    for magic, ext, _unused in SIGNATURES:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


@bp.post("/studio/upload")
@login_required
def upload():
    if not deps.can_author():
        abort(404)
    f = request.files.get("file")
    if f is None:
        return jsonify(error=_("No file was sent.")), 400
    data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return jsonify(error=_('Images can be at most %(value)s MB.', value=MAX_BYTES // (1024 * 1024))), 413
    ext = sniff(data[:16])
    if ext is None:
        return jsonify(error=_("Only PNG, JPEG, GIF and WebP images can be added.")), 415
    name = f"{secrets.token_urlsafe(12)}.{ext}"
    (media_dir() / name).write_bytes(data)
    alt = Path(f.filename or "image").stem.replace("[", "").replace("]", "")[:80] or "image"
    url = f"/media/{name}"
    return jsonify(url=url, markdown=f"![{alt}]({url})")


@bp.get("/media/<name>")
def serve(name: str):
    ext = name.rsplit(".", 1)[-1].lower()
    if "/" in name or "\\" in name or ext not in MIMES or not name[:-len(ext) - 1].replace("-", "").replace("_", "").isalnum():
        abort(404)
    resp = send_from_directory(media_dir(), name, mimetype=MIMES[ext], max_age=31536000)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return resp


def init_app(app) -> None:
    app.config.setdefault("MAX_CONTENT_LENGTH", MAX_BYTES + 512 * 1024)
    app.register_blueprint(bp)
