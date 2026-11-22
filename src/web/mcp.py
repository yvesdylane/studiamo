"""MCP server: lets a lecturer's own AI app work on their notes.

    POST /mcp      JSON-RPC 2.0 (MCP "Streamable HTTP", plain JSON answers)
    Authorization: Bearer stm_...      (a key from Settings > AI)

The AI can read the Studiamo Markdown guide, list the lecturer's courses,
check Markdown with the real renderer, and create or update DRAFT notes,
which then go through admin review like any other note. A key always acts as
a lecturer on their own courses: never as an admin, never with a session
cookie (cookies are ignored here, and so is CSRF, which protects cookies).

Tools: get_markdown_guide, list_courses, check_markdown, read_note,
create_topic, save_note, submit_note_for_review. Prompt: convert_notes.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, url_for

from src.base.ServiceErrors import ServiceError
from src.content.StudiamoMarkdown import render
from src.course.dto.NoteDto import NoteCreate, NoteUpdate
from src.course.dto.TopicDto import TopicCreate
from src.progress.service.ExpRules import DEFAULT_EXP
from src.user.models.User import UserRole
from src.user.service.ApiTokenService import ApiTokenService
from src.web import deps

bp = Blueprint("mcp", __name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "studiamo", "title": "Studiamo notes", "version": "1.0.0"}
MAX_NOTE_CHARS = 400_000
GUIDE = Path(__file__).resolve().parents[2] / "docs" / "studiamo-markdown.md"

INSTRUCTIONS = (
    "Studiamo is a study platform. Notes are written in Studiamo Markdown: normal Markdown plus code boxes "
    '("""python ... """ that readers can run), file trees, live diagrams and note boxes. Read get_markdown_guide '
    "before writing, check your result with check_markdown, then save it with save_note. Saved notes are drafts: "
    "an admin reviews them before students see them. Keep the author's content and meaning; only change the form."
)


class McpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ================================================================== tools
def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS = [
    {"name": "get_markdown_guide", "title": "Studiamo Markdown guide",
     "description": "The full guide to Studiamo Markdown (code boxes, IDE, file trees, live diagrams, note boxes, side by side). Read it before converting notes.",
     "inputSchema": _schema({}, []), "annotations": {"readOnlyHint": True}},
    {"name": "list_courses", "title": "My courses",
     "description": "The courses this lecturer can edit, with their topics and notes (ids, titles, review status).",
     "inputSchema": _schema({}, []), "annotations": {"readOnlyHint": True}},
    {"name": "check_markdown", "title": "Check Markdown",
     "description": "Render Studiamo Markdown exactly like the site does. Returns the problems found in interactive blocks (fix them before saving) and the section headings.",
     "inputSchema": _schema({"markdown": {"type": "string"}}, ["markdown"]), "annotations": {"readOnlyHint": True}},
    {"name": "read_note", "title": "Read a note",
     "description": "The current Markdown of one of the lecturer's notes.",
     "inputSchema": _schema({"note_id": {"type": "integer"}}, ["note_id"]), "annotations": {"readOnlyHint": True}},
    {"name": "create_topic", "title": "Create a topic",
     "description": "Add a topic (a week or a chapter) at the end of one of the lecturer's courses.",
     "inputSchema": _schema({"course_id": {"type": "integer"}, "title": {"type": "string", "maxLength": 200}}, ["course_id", "title"])},
    {"name": "save_note", "title": "Save a note (draft)",
     "description": "Create a new draft note in a topic (give topic_id), or replace the text of an existing note (give note_id). "
                    "The note is a draft until an admin approves it; changing an approved note sends it back to review.",
     "inputSchema": _schema({"title": {"type": "string", "maxLength": 200}, "markdown": {"type": "string"},
                             "topic_id": {"type": "integer"}, "note_id": {"type": "integer"}}, ["title", "markdown"])},
    {"name": "submit_note_for_review", "title": "Submit for review",
     "description": "Send a draft note to the admins for review (they publish it and set its exp).",
     "inputSchema": _schema({"note_id": {"type": "integer"}}, ["note_id"])},
]

PROMPTS = [{
    "name": "convert_notes", "title": "Convert my notes to Studiamo",
    "description": "Turn notes you already have (pasted text, Word, slides…) into a Studiamo note.",
    "arguments": [{"name": "notes", "description": "The notes to convert", "required": True},
                  {"name": "topic", "description": "Where to save it (topic name or id), optional", "required": False}],
}]


def _owned_course(user, course_id: int):
    course = deps.course_or_none(course_id)
    if course is None or course.created_by != user.id:
        raise McpError(-32602, f"Course {course_id} is not one of your courses")
    return course


def _owned_note(user, note_id: int):
    try:
        note = deps.notes().get(note_id)
        topic = deps.topics().get(note.topic_id)
    except ServiceError:
        raise McpError(-32602, f"Note {note_id} does not exist")
    _owned_course(user, topic.course_id)
    return note


def _text(value) -> dict:
    return {"content": [{"type": "text", "text": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)}]}


def call_tool(user, name: str, args: dict) -> dict:
    if name == "get_markdown_guide":
        return _text(GUIDE.read_text(encoding="utf-8"))
    if name == "list_courses":
        courses = [c for c in deps.courses().list(limit=100) if c.created_by == user.id]
        data = []
        for c in courses:
            notes = deps.notes().list_for_course(c.id)
            data.append({"course_id": c.id, "code": c.code, "title": c.title, "topics": [
                {"topic_id": t.id, "title": t.title, "notes": [
                    {"note_id": n.id, "title": n.title, "status": "published" if n.published else n.review_status}
                    for n in sorted(notes, key=lambda n: n.position) if n.topic_id == t.id]}
                for t in deps.topics().list_for_course(c.id)]})
        return _text(data or "You have no courses yet. Create one in the Studio first.")
    if name == "check_markdown":
        page = render(str(args.get("markdown", ""))[:MAX_NOTE_CHARS])
        return _text({"problems": page.problems, "sections": [t for _, t in page.toc],
                      "ok": not page.problems})
    if name == "read_note":
        note = _owned_note(user, int(args["note_id"]))
        try:
            text = deps.notes().read_content(note.id)
        except FileNotFoundError:
            text = ""
        return _text({"note_id": note.id, "title": note.title, "markdown": text})
    if name == "create_topic":
        _owned_course(user, int(args["course_id"]))
        existing = deps.topics().list_for_course(int(args["course_id"]))
        topic = deps.topics().create(TopicCreate(course_id=int(args["course_id"]), title=str(args["title"]).strip()[:200] or "New topic",
                                                 position=len(existing)))
        return _text({"topic_id": topic.id, "title": topic.title})
    if name == "save_note":
        markdown = str(args.get("markdown", ""))
        if len(markdown) > MAX_NOTE_CHARS:
            raise McpError(-32602, f"The note is too long (at most {MAX_NOTE_CHARS} characters)")
        title = str(args.get("title", "")).strip()[:200] or "Untitled"
        if args.get("note_id") is not None:
            note = _owned_note(user, int(args["note_id"]))
            deps.notes().save_content(note.id, markdown)
            deps.notes().update(note.id, NoteUpdate(title=title))
            reopened = deps.review().reopen_after_edit("note", note.id)
            note_id = note.id
        else:
            if args.get("topic_id") is None:
                raise McpError(-32602, "Give topic_id (new note) or note_id (existing note)")
            topic = deps.topics().get(int(args["topic_id"]))
            course = _owned_course(user, topic.course_id)
            note = deps.notes().create(NoteCreate(topic_id=topic.id, title=title, position=len(deps.notes().list_for_topic(topic.id)),
                                                  md_path=f"notes/{course.id}/{uuid.uuid4().hex}.md", max_exp=DEFAULT_EXP["note"]))
            deps.notes().save_content(note.id, markdown)
            reopened, note_id = False, note.id
        problems = render(markdown).problems
        return _text({"note_id": note_id, "status": "draft (in review again)" if reopened else "draft",
                      "editor_url": url_for("studio.edit_note", note_id=note_id, _external=True),
                      "problems": problems})
    if name == "submit_note_for_review":
        note = _owned_note(user, int(args["note_id"]))
        try:
            deps.review().submit("note", note.id)
        except ServiceError as exc:
            raise McpError(-32602, str(exc))
        return _text({"note_id": note.id, "status": "pending review"})
    raise McpError(-32602, f"Unknown tool {name!r}")


def get_prompt(name: str, args: dict) -> dict:
    if name != "convert_notes":
        raise McpError(-32602, f"Unknown prompt {name!r}")
    where = f" Save it in this topic: {args['topic']}." if args.get("topic") else " Ask me which topic to save it in (list_courses shows them)."
    text = ("Convert the notes below into one Studiamo note. First call get_markdown_guide. Keep my content and meaning; "
            "use a # title, ## sections, code boxes for code (runnable for Python/JavaScript), file trees for folder "
            "structures, live diagrams where something depends on numbers, and note boxes for key ideas. Check it with "
            "check_markdown and fix every problem, then save it with save_note as a draft." + where + "\n\nMY NOTES:\n\n" + str(args.get("notes", "")))
    return {"description": "Convert notes to Studiamo Markdown", "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


# =============================================================== protocol
def _user_from_key():
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    user_id = ApiTokenService(deps.db()).authenticate(auth[7:].strip())
    if user_id is None:
        return None
    try:
        user = deps.users().get(user_id)
    except ServiceError:
        return None
    return user if UserRole.LECTURER in user.roles else None   # authoring needs the lecturer role


def _handle(msg: dict, user) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {"code": -32600, "message": "Invalid request"}}
    method, params, mid = msg["method"], msg.get("params") or {}, msg.get("id")
    if mid is None:                       # a notification: no answer
        return None
    try:
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                      "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                result = call_tool(user, params.get("name", ""), params.get("arguments") or {})
            except McpError as exc:      # tool errors are shown to the model, not protocol errors
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            except (ServiceError, ValueError, KeyError, TypeError) as exc:
                result = {"content": [{"type": "text", "text": f"Could not do that: {exc}"}], "isError": True}
        elif method == "prompts/list":
            result = {"prompts": PROMPTS}
        elif method == "prompts/get":
            result = get_prompt(params.get("name", ""), params.get("arguments") or {})
        elif method in ("resources/list", "resources/templates/list"):
            result = {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
        else:
            raise McpError(-32601, f"Method not found: {method}")
    except McpError as exc:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": exc.code, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


@bp.route("/mcp", methods=["POST", "GET", "DELETE"])
def endpoint():
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return jsonify(error="Origin not allowed"), 403        # browsers on other sites (DNS rebinding)
    user = _user_from_key()
    if user is None:
        resp = jsonify(jsonrpc="2.0", id=None, error={"code": -32001, "message": "A valid Studiamo key (Settings > AI) of a lecturer account is needed"})
        resp.status_code = 401
        resp.headers["WWW-Authenticate"] = 'Bearer realm="studiamo"'
        return resp
    if request.method != "POST":
        return Response(status=405, headers={"Allow": "POST"})   # no server-sent event stream
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify(jsonrpc="2.0", id=None, error={"code": -32700, "message": "Parse error"}), 400
    if isinstance(payload, list):
        answers = [a for a in (_handle(m, user) for m in payload) if a is not None]
        return (jsonify(answers), 200) if answers else Response(status=202)
    answer = _handle(payload, user)
    if answer is None:
        return Response(status=202)
    resp = jsonify(answer)
    resp.headers["MCP-Protocol-Version"] = request.headers.get("MCP-Protocol-Version", PROTOCOL_VERSIONS[0])
    return resp


def init_app(app) -> None:
    app.register_blueprint(bp)
    app.config.setdefault("CSRF_EXEMPT_PATHS", set()).add("/mcp")
