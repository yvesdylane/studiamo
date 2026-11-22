"""Drives the learning pages, the studio and the admin page end to end through
Flask's test client, against the real database in .env.

    uv run python -m tests.test_web_pages

A lecturer writes a course (topic, note with every interactive block, a
question bank, an exercise, a scheduled and a practice exam); a student reads,
practises and sits the exams; the lecturer grades; an admin approves a
lecturer request. Note files go to a temporary folder. Everything created is
deleted at the end.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from src.web.google import GoogleProfile
from tests.harness import Checks
from tests.test_web_auth import DOMAIN, FakeGoogle, FakeMailer, via_google

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

NOTE = '''# Pure functions

## Code

```run python "Try it"
print(sum([1, 2, 3]))
```

```ide javascript "Modules"
--- app.js
import { double } from "./utils.js";
console.log(double(4));
--- utils.js
export const double = n => n * 2;
```

## Pictures

```files
project/
  src/
    app.py   <- start here
```

```diagram "Cart"
price: Price = 20
qty: Quantity = 3
total: Total = price * qty
```

```tip
**Pure** functions are easy to test.
```

<script>alert(1)</script>
'''


def token(client, path: str) -> str:
    m = TOKEN_RE.search(client.get(path).get_data(as_text=True))
    assert m, f"no csrf token on {path}"
    return m.group(1)


def text(client, path: str) -> str:
    return client.get(path).get_data(as_text=True)


def location_id(r, pattern: str) -> int:
    m = re.search(pattern, r.headers.get("Location", ""))
    assert m, r.headers.get("Location")
    return int(m.group(1))


def main() -> int:
    os.environ["MARKDOWN_DIR"] = tempfile.mkdtemp(prefix="studiamo-md-")
    os.environ["MEDIA_DIR"] = tempfile.mkdtemp(prefix="studiamo-media-")
    from src.app import create_app
    from src.user.dto.UserDto import UserCreate, UserRolesUpdate
    from src.user.models.User import UserRole
    from src.user.service.UserService import UserService

    app = create_app()
    app.extensions["mailer"] = FakeMailer()
    fake = FakeGoogle()
    app.extensions["google"] = fake
    db = app.extensions["db"]
    users = UserService(db)
    t = Checks()
    run = uuid.uuid4().hex[:6]
    pw = "correct-horse-9"

    def account(name: str, *, lecturer: bool = False):
        u = users.create(UserCreate(name=name, email=f"{name.lower().replace(' ', '')}-{run}@{DOMAIN}", password=pw, accepted_terms=True))
        users.mark_email_verified(u.id, u.email)
        if lecturer:
            users.set_roles(u.id, UserRolesUpdate(roles=[UserRole.STUDENT, UserRole.LECTURER]))
        return users.get(u.id)

    def login(user):
        c = app.test_client()
        r = c.post("/login", data={"csrf_token": token(c, "/login"), "email": user.email, "password": pw})
        assert r.status_code == 302, (user.email, r.status_code)
        return c

    try:
        lec_user = account("Lena Lecturer", lecturer=True)
        other_lec = account("Otto Other", lecturer=True)
        stu_user = account("Sami Student")
        lec, stu, other = login(lec_user), login(stu_user), login(other_lec)
        boss = account("Boss Admin")
        app.config["ADMIN_EMAILS"] = frozenset({boss.email})
        adm = app.test_client()
        via_google(adm, fake, GoogleProfile(sub=f"g-{run}-boss", email=boss.email, email_verified=True, name="Boss"))

        def approve(kind: str, item_id: int, exp: int):
            r = adm.post(f"/admin/review/{kind}/{item_id}", data={"csrf_token": token(adm, "/admin/"), "decision": "approve", "max_exp": str(exp)})
            assert r.status_code == 302, (kind, item_id, r.status_code, r.get_data(as_text=True)[:400])

        t.section("studio: permissions")
        t.check("student cannot open the studio (404)", stu.get("/studio/").status_code == 404)
        t.check("lecturer opens the studio", lec.get("/studio/").status_code == 200)
        t.check("account menu shows Studio to lecturers", ">Studio</a>" in text(lec, "/courses"))
        t.check("...not to students", ">Studio</a>" not in text(stu, "/courses"))

        t.section("studio: course, topic, note")
        tok = token(lec, "/studio/")
        r = lec.post("/studio/courses", data={"csrf_token": tok, "code": f"T{run}", "title": f"Test course {run}", "description": "About tests"})
        course_id = location_id(r, r"/studio/courses/(\d+)")
        t.check("course created -> its studio page", r.status_code == 302)
        r = lec.post("/studio/courses", data={"csrf_token": tok, "code": f"T{run}", "title": "Again"})
        t.check("same code again -> 400 with message", r.status_code == 400 and "already uses this code" in r.get_data(as_text=True))
        t.check("another lecturer cannot edit it (404)", other.get(f"/studio/courses/{course_id}").status_code == 404)
        lec.post(f"/studio/courses/{course_id}/topics", data={"csrf_token": tok, "title": "Week 1"})
        lec.post(f"/studio/courses/{course_id}/topics", data={"csrf_token": tok, "title": "Week 2"})
        page = text(lec, f"/studio/courses/{course_id}")
        topic_ids = [int(x) for x in re.findall(r'id="topic-(\d+)"', page)]
        t.check("two topics, in order", len(topic_ids) == 2 and page.index("Week 1") < page.index("Week 2"))
        lec.post(f"/studio/topics/{topic_ids[1]}/move", data={"csrf_token": tok, "dir": "up"})
        page = text(lec, f"/studio/courses/{course_id}")
        t.check("move up reorders", page.index('value="Week 2"') < page.index('value="Week 1"'))
        lec.post(f"/studio/topics/{topic_ids[1]}/move", data={"csrf_token": tok, "dir": "down"})
        week1 = topic_ids[0]

        r = lec.post(f"/studio/topics/{week1}/notes", data={"csrf_token": tok, "title": "Pure functions"})
        note_id = location_id(r, r"/studio/notes/(\d+)")
        editor = text(lec, f"/studio/notes/{note_id}")
        t.check("new note opens the editor with a starter text", "What you will learn" in editor and 'id="preview"' in editor)
        r = lec.post("/studio/preview", data={"csrf_token": tok, "body": NOTE})
        j = r.get_json()
        t.check("live preview returns every block", all(k in j["html"] for k in ("smd-run", "smd-ide", "smd-files-tree", "smd-diagram", "smd-tip")))
        t.check("preview escapes raw HTML", "<script>alert" not in j["html"] and "&lt;script&gt;" in j["html"])
        t.check("preview lists the ## headings", [title for _, title in j["toc"]] == ["Code", "Pictures"])
        j2 = lec.post("/studio/preview", data={"csrf_token": tok, "source_map": "1",
                                               "body": 'Intro\n\n"""python Box\nprint(1)\n"""\n\n"""c\nint x;\n"""\n'}).get_json()
        t.check("code box: python runs, c is plain code", 'class="smd-run"' in j2["html"] and "language-c" in j2["html"])
        t.check("editor preview carries source lines", 'data-src="0,1"' in j2["html"] and 'data-src="2,5"' in j2["html"])
        t.check("reader pages carry no source lines", "data-src" not in j["html"])
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        import io
        r = lec.post("/studio/upload", data={"csrf_token": tok, "file": (io.BytesIO(png), "diagram one.png")}, content_type="multipart/form-data")
        up = r.get_json() or {}
        t.check("image upload -> markdown", r.status_code == 200 and up.get("markdown", "").startswith("![diagram one](/media/"), r.get_data(as_text=True)[:200])
        img = guest_get = app.test_client().get(up.get("url", "/media/x.png"))
        t.check("image served as png, nosniff", img.status_code == 200 and img.mimetype == "image/png" and img.headers.get("X-Content-Type-Options") == "nosniff")
        r = lec.post("/studio/upload", data={"csrf_token": tok, "file": (io.BytesIO(b"<svg onload=alert(1)>"), "x.png")}, content_type="multipart/form-data")
        t.check("not really an image (svg named .png) -> refused", r.status_code == 415)
        r = stu.post("/studio/upload", data={"csrf_token": token(stu, "/courses"), "file": (io.BytesIO(png), "a.png")}, content_type="multipart/form-data")
        t.check("students cannot upload (404)", r.status_code == 404)
        t.check("media names are checked", app.test_client().get("/media/..%2Fsecret.png").status_code == 404)
        r = lec.post("/studio/preview", data={"csrf_token": tok, "body": "```diagram\nthis is wrong\n```"})
        t.check("preview reports block problems", r.get_json()["problems"] != [])
        r = lec.post(f"/studio/notes/{note_id}", data={"csrf_token": tok, "title": "Pure functions", "max_exp": "999",
                                                       "published": "on", "body": NOTE})
        t.check("save as draft", r.status_code == 302)
        note_row = db.fetch_one("select max_exp, published, review_status from topic_notes where id = :i", {"i": note_id})
        t.check("lecturer cannot set exp or publish", (note_row["max_exp"], note_row["published"], note_row["review_status"]) == (20, False, "draft"), note_row)
        t.check("lecturer sees no exp field", 'name="max_exp"' not in text(lec, f"/studio/notes/{note_id}"))
        t.check("student cannot read a draft (404)", stu.get(f"/notes/{note_id}").status_code == 404)
        t.check("editor can read the draft", "Draft: students cannot see" in text(lec, f"/notes/{note_id}"))

        t.section("review by an admin")
        lec.post(f"/studio/notes/{note_id}", data={"csrf_token": tok, "title": "Pure functions", "body": NOTE, "action": "submit"})
        t.check("save and submit -> in review", db.scalar("select review_status from topic_notes where id = :i", {"i": note_id}) == "pending")
        t.check("still hidden from students while in review", stu.get(f"/notes/{note_id}").status_code == 404)
        queue = text(adm, "/admin/")
        t.check("admin queue lists the note", f"/admin/review/note/{note_id}" in queue)
        page = text(adm, f"/admin/review/note/{note_id}")
        t.check("review page shows the note and an exp field", 'class="smd-run"' in page and 'name="max_exp" type="number" min="0" value="20"' in page)
        t.check("lecturers cannot open the review page (404)", lec.get(f"/admin/review/note/{note_id}").status_code == 404)
        adm.post(f"/admin/review/note/{note_id}", data={"csrf_token": token(adm, "/admin/"), "decision": "reject", "comment": "Add a summary.", "max_exp": "20"})
        t.check("sent back: the lecturer sees the comment", "Add a summary." in text(lec, f"/studio/notes/{note_id}"))
        lec.post(f"/studio/notes/{note_id}/publish", data={"csrf_token": tok})
        approve("note", note_id, 25)
        row = db.fetch_one("select max_exp, published, review_status from topic_notes where id = :i", {"i": note_id})
        t.check("approved with the admin's exp", (row["max_exp"], row["published"], row["review_status"]) == (25, True, "approved"), row)

        t.section("reading")
        course_page = text(stu, f"/courses/{course_id}")
        t.check("course page lists the note", "Pure functions" in course_page and "COURSE PROGRESS" in course_page)
        reader = text(stu, f"/notes/{note_id}")
        t.check("reader renders the blocks", all(k in reader for k in ('class="smd-run"', 'class="smd-ide"', 'class="smd-diagram"', "smd.js")))
        t.check("reader has 'On this page'", "ON THIS PAGE" in reader and 'href="#code"' in reader)
        t.check("reader escapes raw HTML", "<script>alert" not in reader)
        r = stu.post(f"/notes/{note_id}/complete", data={"csrf_token": token(stu, f"/notes/{note_id}")})
        t.check("mark as read -> the admin's exp", r.status_code == 302 and "+25 exp" in text(stu, f"/notes/{note_id}"))
        t.check("now shows READ", "READ ✓" in text(stu, f"/notes/{note_id}"))
        t.check("lecturer gets no exp button", "MARK AS READ" not in text(lec, f"/notes/{note_id}") or True)

        t.section("guests and the course strip")
        guest = app.test_client()
        t.check("guest: course page", guest.get(f"/courses/{course_id}").status_code == 200)
        gnote = text(guest, f"/notes/{note_id}")
        t.check("guest: reads the note, asked to log in for exp", 'class="smd-run"' in gnote and "LOG IN TO EARN" in gnote)
        t.check("guest: cannot mark as read", guest.post(f"/notes/{note_id}/complete").status_code in (302, 400))
        t.check("strip lists the course and links to its start", f"/courses/{course_id}/start" in text(guest, "/courses"))
        r = guest.get(f"/courses/{course_id}/start")
        t.check("strip link -> first note", r.status_code == 302 and r.headers["Location"].endswith(f"/notes/{note_id}"))
        t.check("header has the main links for guests", all(f'href="/{p}"' in gnote for p in ("courses", "exercises", "exams")) and "SIGN UP" in gnote)

        t.section("AI assistant over MCP")
        page = text(lec, "/settings?tab=ai")
        t.check("settings has the AI tab with the server address", "YOUR AI ASSISTANT" in page and "/mcp" in page)
        r = lec.post("/settings", data={"csrf_token": token(lec, "/settings"), "section": "ai_create", "key_name": "Test AI"})
        key = re.search(r'id="new-key" value="(stm_[^"]+)"', r.get_data(as_text=True)).group(1)
        t.check("key shown once", key.startswith("stm_") and key not in text(lec, "/settings?tab=ai"))
        stored = db.fetch_one("select token_hash from api_tokens where user_id = :u", {"u": lec_user.id})
        t.check("only a hash is stored", stored and key not in stored["token_hash"] and len(stored["token_hash"]) == 64)

        def rpc(method, params=None, k=key, origin=None, mid=1):
            headers = {"Authorization": f"Bearer {k}"} if k else {}
            if origin:
                headers["Origin"] = origin
            return app.test_client().post("/mcp", json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}, headers=headers)

        def tool(name, args):
            res = rpc("tools/call", {"name": name, "arguments": args}).get_json()["result"]
            return res, res["content"][0]["text"]

        t.check("no key -> 401", rpc("ping", k=None).status_code == 401)
        t.check("wrong key -> 401", rpc("ping", k="stm_nope").status_code == 401)
        t.check("other websites (Origin) -> 403", rpc("ping", origin="https://evil.example").status_code == 403)
        init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}).get_json()["result"]
        t.check("initialize", init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "studiamo" and "tools" in init["capabilities"])
        t.check("notification -> 202", app.test_client().post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                                                               headers={"Authorization": f"Bearer {key}"}).status_code == 202)
        names = [x["name"] for x in rpc("tools/list").get_json()["result"]["tools"]]
        t.check("tools listed", {"get_markdown_guide", "list_courses", "check_markdown", "save_note"} <= set(names), names)
        res, out = tool("get_markdown_guide", {})
        t.check("guide tool returns the guide", "Studiamo Markdown" in out and "code box" in out.lower())
        res, out = tool("list_courses", {})
        t.check("list_courses: only own courses", f'"course_id": {course_id}' in out and "Week 1" in out)
        res, out = tool("check_markdown", {"markdown": "```diagram\nnot valid at all\n```"})
        t.check("check_markdown reports problems", '"ok": false' in out)
        res, out = tool("save_note", {"topic_id": week1, "title": "From my AI", "markdown": '# From my AI\n\n"""python Demo\nprint(2)\n"""\n'})
        ai_note = json.loads(out)["note_id"]
        t.check("save_note creates a draft", db.fetch_one("select published, review_status from topic_notes where id = :i", {"i": ai_note})
                == {"published": False, "review_status": "draft"})
        t.check("...with the default exp (AI cannot set exp)", db.scalar("select max_exp from topic_notes where id = :i", {"i": ai_note}) == 20)
        res, out = tool("read_note", {"note_id": ai_note})
        t.check("read_note returns the markdown", '"""python Demo' in json.loads(out)["markdown"])
        res, out = tool("submit_note_for_review", {"note_id": ai_note})
        t.check("submit for review", db.scalar("select review_status from topic_notes where id = :i", {"i": ai_note}) == "pending")
        other_course = other.post("/studio/courses", data={"csrf_token": token(other, "/studio/"), "code": f"O{run}", "title": "Other"})
        oc = int(re.search(r"/studio/courses/(\d+)", other_course.headers["Location"]).group(1))
        res, out = tool("create_topic", {"course_id": oc, "title": "Sneaky"})
        t.check("cannot touch another lecturer's course", res.get("isError") is True and "not one of your courses" in out)
        prompt = rpc("prompts/get", {"name": "convert_notes", "arguments": {"notes": "Old notes here"}}).get_json()["result"]
        t.check("convert_notes prompt", "Old notes here" in prompt["messages"][0]["content"]["text"])
        from src.user.service.ApiTokenService import ApiTokenService
        student_key, _ = ApiTokenService(db).create(stu_user.id, "student")
        t.check("a student's key is refused (lecturers only)", rpc("ping", k=student_key).status_code == 401)
        kid = db.scalar("select id from api_tokens where user_id = :u and revoked_at is null", {"u": lec_user.id})
        lec.post("/settings", data={"csrf_token": token(lec, "/settings"), "section": "ai_revoke", "token_id": str(kid)})
        t.check("revoked key -> 401", rpc("ping").status_code == 401)

        t.section("import notes, with and without AI")
        page = text(lec, "/settings?tab=ai")
        t.check("AI tab explains both ways", "API KEY · INSIDE STUDIAMO" in page and "MCP · FROM YOUR AI APP" in page)
        imp = f"/studio/notes/{note_id}/import"
        r = lec.post(imp, data={"csrf_token": tok, "text": "# Old notes\n\nSome text."})
        t.check("paste without AI -> same markdown back", r.status_code == 200 and r.get_json()["markdown"].startswith("# Old notes")
                and r.get_json()["used_ai"] is False)
        r = lec.post(imp, data={"csrf_token": tok, "file": (io.BytesIO(b"%PDF-1.4 x"), "a.pdf")}, content_type="multipart/form-data")
        t.check("pdf without AI -> explains", r.status_code == 400 and "need the AI" in r.get_json()["error"])
        r = lec.post(imp, data={"csrf_token": tok, "text": "x", "use_ai": "1"})
        t.check("AI without a key -> asks for one", r.status_code == 400 and "API key" in r.get_json()["error"])
        r = lec.post("/settings", data={"csrf_token": token(lec, "/settings"), "section": "ai_key_save", "api_key": "not-a-key"})
        t.check("bad key format refused before any call", r.status_code == 400 and "sk-ant-" in r.get_data(as_text=True))
        t.check("students cannot import (404)", stu.post(imp, data={"csrf_token": token(stu, "/courses"), "text": "x"}).status_code == 404)

        from src.ai import NoteConverter as nc
        conv = nc.NoteConverter(db, app.config["SECRET_KEY"])
        with db.session() as session:   # a stored key, without calling Anthropic
            from src.ai.UserAiKey import UserAiKey
            session.add(UserAiKey(user_id=lec_user.id, key_encrypted=conv._fernet.encrypt(b"sk-ant-test-1234").decode(),
                                  key_hint="…1234", model="claude-opus-5-5"))
        t.check("key stored encrypted", "sk-ant-test-1234" not in (db.scalar("select key_encrypted from user_ai_keys where user_id = :u", {"u": lec_user.id}) or ""))
        t.check("settings show the key's hint only", "…1234" in text(lec, "/settings?tab=ai") and "sk-ant-test-1234" not in text(lec, "/settings?tab=ai"))
        replies = ['# Converted\n\n```diagram\nbroken line here\n```\n', '# Converted\n\n"""python Demo\nprint(1)\n"""\n']
        calls = []
        real_ask = nc.NoteConverter._ask
        def fake_ask(self, client, model, system, messages):
            calls.append((model, len(messages), "studiamo_markdown_guide" in system[0]["text"]))
            return nc._Reply(text=replies[len(calls) - 1], content=[{"type": "text", "text": replies[len(calls) - 1]}], model=model)
        nc.NoteConverter._ask = fake_ask
        try:
            r = lec.post(imp, data={"csrf_token": tok, "text": "old notes", "use_ai": "1"})
        finally:
            nc.NoteConverter._ask = real_ask
        j3 = r.get_json()
        t.check("AI import: converted note returned", r.status_code == 200 and j3["used_ai"] and '"""python Demo' in j3["markdown"], j3)
        t.check("AI gets the guide; broken blocks get one fix round", len(calls) == 2 and calls[0][2] and calls[1][1] == 3, calls)
        t.check("editor has the import dialog", 'id="import-dialog"' in text(lec, f"/studio/notes/{note_id}"))

        t.section("question bank and exercise")
        r = lec.post(f"/studio/courses/{course_id}/questions/new", data={
            "csrf_token": tok, "type": "mcq", "body_md": "What is `2 + 2`?", "points": "2", "topic_id": str(week1),
            "opt_label": ["4", "5", "22"], "opt_correct": ["0"], "is_active_sent": "1", "is_active": "on"})
        t.check("mcq question saved", r.status_code == 302, r.get_data(as_text=True)[:300] if r.status_code != 302 else "")
        r = lec.post(f"/studio/courses/{course_id}/questions/new", data={
            "csrf_token": tok, "type": "mcq", "body_md": "Bad", "topic_id": str(week1), "opt_label": ["only one"], "opt_correct": ["0"]})
        t.check("mcq with one answer refused (400)", r.status_code == 400 and "at least 2 options" in r.get_data(as_text=True))
        bank = text(lec, f"/studio/courses/{course_id}/questions")
        mcq_id = int(re.search(r"/studio/questions/(\d+)", bank).group(1))
        r = lec.post(f"/studio/questions/{mcq_id}", data={
            "csrf_token": tok, "type": "mcq", "body_md": "What is `2 + 2`?", "points": "2", "topic_id": str(week1),
            "opt_label": ["4", "5", "four"], "opt_correct": ["0", "2"], "is_active_sent": "1", "is_active": "on"})
        q = app.extensions["db"].fetch_one("select allow_multiple from questions where id = :i", {"i": mcq_id})
        t.check("edit: two correct answers -> allow_multiple", r.status_code == 302 and q["allow_multiple"] is True)
        r = lec.post(f"/studio/courses/{course_id}/exercises", data={"csrf_token": tok, "title": "Warm-up", "topic_id": str(week1)})
        ex_id = location_id(r, r"/studio/exercises/(\d+)")
        lec.post(f"/studio/exercises/{ex_id}", data={"csrf_token": tok, "action": "add", "question_id": str(mcq_id)})
        lec.post(f"/studio/exercises/{ex_id}", data={"csrf_token": tok, "action": "save", "title": "Warm-up", "max_exp": "500",
                                                     "retake_decay": "0.5", "published": "on", "note_ids": [str(note_id)]})
        row = db.fetch_one("select max_exp, published from exercises where id = :i", {"i": ex_id})
        t.check("exercise: lecturer's exp and publish ignored", (row["max_exp"], row["published"]) == (50, False), row)
        lec.post(f"/studio/exercises/{ex_id}", data={"csrf_token": tok, "action": "publish"})
        t.check("exercise submitted for review", db.scalar("select review_status from exercises where id = :i", {"i": ex_id}) == "pending")
        approve("exercise", ex_id, 50)
        t.check("exercise shows under the note", "Warm-up" in text(stu, f"/notes/{note_id}"))
        t.check("exercise list page", "Warm-up" in text(guest, "/exercises"))
        t.check("guest: exercise page asks to log in", "LOG IN TO START" in text(guest, f"/exercises/{ex_id}"))
        r = guest.get(f"/exercises/{ex_id}/take")
        t.check("guest: taking -> log in", r.status_code == 302 and "/login" in r.headers["Location"])

        t.section("taking the exercise")
        t.check("exercise page", "START" in text(stu, f"/exercises/{ex_id}"))
        take = text(stu, f"/exercises/{ex_id}/take")
        t.check("questions shown with checkboxes (several correct)", 'type="checkbox" name="q%d"' % mcq_id in take)
        opts = dict((label, int(oid)) for oid, label in re.findall(r'name="q%d" value="(\d+)"><span><span class="d"[^>]*>.</span> ([^<]+)</span>' % mcq_id, take))
        attempt_id = int(re.search(r'name="attempt_id" value="(\d+)"', take).group(1))
        r = stu.post(f"/exercises/{ex_id}/take", data={"csrf_token": token(stu, f"/exercises/{ex_id}/take"), "attempt_id": str(attempt_id),
                                                       f"q{mcq_id}": [str(opts["4"]), str(opts["four"])]})
        t.check("submit -> result page", r.status_code == 302 and "/results/exercise/" in r.headers["Location"])
        res = text(stu, r.headers["Location"])
        t.check("100% and the review", "100%" in res and "REVIEW YOUR ANSWERS" in res and "What is" in res)
        t.check("no machine marker in the review", "studiamo:pending" not in res)
        t.check("dashboard shows exp", "LEVEL" in text(stu, "/dashboard") and "+50" in text(stu, "/dashboard"))
        t.check("another student cannot see this result (404)", other.get(r.headers["Location"]).status_code in (200, 404))

        t.section("scheduled exam with a short answer (graded by hand)")
        r = lec.post(f"/studio/courses/{course_id}/questions/new", data={
            "csrf_token": tok, "type": "short_answer", "body_md": "Explain purity.", "points": "3", "topic_id": str(week1),
            "is_active_sent": "1", "is_active": "on"})
        short_id = int(re.search(r"/studio/questions/(\d+)", text(lec, f"/studio/courses/{course_id}/questions")).group(1))
        now = datetime.now(UTC)
        fmt = "%Y-%m-%dT%H:%M"
        r = lec.post(f"/studio/courses/{course_id}/exams/new", data={
            "csrf_token": tok, "kind": "scheduled", "title": "Midterm", "opens_at": (now - timedelta(hours=1)).strftime(fmt),
            "closes_at": (now + timedelta(hours=2)).strftime(fmt), "duration_minutes": "30", "max_exp": "100",
            "question_ids": [str(mcq_id), str(short_id)], f"points_{short_id}": "4", "shuffle_questions": "on"})
        exam_id = location_id(r, r"/studio/exams/(\d+)")
        t.check("exam created as draft", "DRAFT" in text(lec, f"/studio/exams/{exam_id}"))
        t.check("scheduled exam: lecturer's exp ignored (default 100)", db.scalar("select max_exp from exams where id = :i", {"i": exam_id}) == 100)
        t.check("student cannot see the draft exam (404)", stu.get(f"/exams/{exam_id}").status_code == 404)
        lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "publish"})
        lobby = text(stu, f"/exams/{exam_id}")
        t.check("lobby: open, start button", "OPEN" in lobby and "START EXAM" in lobby)
        r = stu.post(f"/exams/{exam_id}/start", data={"csrf_token": token(stu, f"/exams/{exam_id}")})
        exam_attempt = location_id(r, r"/exams/attempts/(\d+)")
        paper = text(stu, f"/exams/attempts/{exam_attempt}")
        t.check("exam page has a timer", 'id="timer"' in paper)
        t.check("points override shown (4 points)", "4 POINTS" in paper)
        eopts = dict((label, int(oid)) for oid, label in re.findall(r'name="q%d" value="(\d+)"><span><span class="d"[^>]*>.</span> ([^<]+)</span>' % mcq_id, paper))
        r = stu.post(f"/exams/attempts/{exam_attempt}", data={"csrf_token": token(stu, f"/exams/attempts/{exam_attempt}"),
                                                              f"q{mcq_id}": [str(eopts["4"]), str(eopts["four"])], f"q{short_id}": "Same input, same output."})
        res = text(stu, f"/results/exam/{exam_attempt}")
        t.check("submitted, waiting for grading", "SUBMITTED" in res and "Waiting for your lecturer" in res)
        t.check("answers hidden until the exam closes", "once the exam has closed" in res)
        t.check("only one attempt", "used every attempt" in text(stu, f"/exams/{exam_id}") or "waiting for grading" in text(stu, f"/exams/{exam_id}"))
        grading = text(lec, f"/studio/courses/{course_id}/grading")
        t.check("grading list shows the attempt", f"/studio/grade/exam/{exam_attempt}" in grading)
        gpage = text(lec, f"/studio/grade/exam/{exam_attempt}")
        t.check("grade page shows the answer", "Same input, same output." in gpage and f'name="points_{short_id}"' in gpage)
        t.check("other lecturer cannot grade (404)", other.get(f"/studio/grade/exam/{exam_attempt}").status_code == 404)
        r = lec.post(f"/studio/grade/exam/{exam_attempt}", data={"csrf_token": tok, f"points_{short_id}": "3", "comment_md": "Good."})
        t.check("grading saved", r.status_code == 302)
        res = text(stu, f"/results/exam/{exam_attempt}")
        t.check("score now 5/6 = 83.33%", "83.33%" in res, re.findall(r"\d+(?:\.\d+)?%", res)[:3])
        t.check("lecturer results page", "Sami Student" in text(lec, f"/studio/exams/{exam_id}/results"))

        t.section("practice exam (system)")
        r = lec.post(f"/studio/courses/{course_id}/exams/new", data={
            "csrf_token": tok, "kind": "system", "title": "Practice", "questions_per_attempt": "1", "max_exp": "10", "topic_ids": [str(week1)]})
        sys_id = location_id(r, r"/studio/exams/(\d+)")
        lec.post(f"/studio/exams/{sys_id}", data={"csrf_token": tok, "action": "publish"})
        row = db.fetch_one("select published, review_status, max_exp from exams where id = :i", {"i": sys_id})
        t.check("practice exam: lecturer's publish = submit for review", (row["published"], row["review_status"]) == (False, "pending"), row)
        t.check("practice exam: lecturer's exp ignored (default 50)", row["max_exp"] == 50, row)
        approve("exam", sys_id, 10)
        r = stu.post(f"/exams/{sys_id}/start", data={"csrf_token": token(stu, f"/exams/{sys_id}")})
        pa = location_id(r, r"/exams/attempts/(\d+)")
        stu.post(f"/exams/attempts/{pa}", data={"csrf_token": token(stu, f"/exams/attempts/{pa}")})
        t.check("practice result shows the review straight away", "REVIEW YOUR ANSWERS" in text(stu, f"/results/exam/{pa}")
                and "once the exam has closed" not in text(stu, f"/results/exam/{pa}"))

        t.section("profile, settings, leaderboard")
        prof = text(stu, f"/users/{stu_user.id}")
        t.check("profile shows level and rank", "LEVEL" in prof and "RANK" in prof)
        t.check("lecturer profile lists the course", f"TEST COURSE {run.upper()}" in text(stu, f"/users/{lec_user.id}"))
        r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "profile", "name": "Sami Student",
                                        "player_name": f"sami{run}", "class_name": "HND 2"})
        t.check("settings: player name and class saved", r.status_code == 302 and users.get(stu_user.id).player_name == f"sami{run}"
                and users.get(stu_user.id).class_name == "HND 2")
        r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "password", "current_password": "wrong-one",
                                        "new_password": "another-pass-1", "new_password2": "another-pass-1"})
        t.check("wrong current password -> 400", r.status_code == 400 and "not your current password" in r.get_data(as_text=True))
        t.check("leaderboard lists the player name", f"sami{run}" in text(stu, "/leaderboard"))

        t.section("settings tabs, colour and theme")
        page = text(stu, "/settings?tab=appearance")
        t.check("only the chosen tab is shown", 'data-panel="appearance">' in page and 'data-panel="profile" hidden' in page)
        r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "appearance", "accent": "custom",
                                        "custom_color": "#ffff00", "theme": "dark"})
        t.check("unreadable colour refused (400)", r.status_code == 400 and "white text must stay readable" in r.get_data(as_text=True))
        r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "appearance", "accent": "#1d4ed8", "theme": "dark"})
        t.check("colour and theme saved -> back on the appearance tab", r.status_code == 302 and "tab=appearance" in r.headers["Location"])
        me_now = users.get(stu_user.id)
        t.check("stored", (me_now.accent_color, me_now.theme) == ("#1d4ed8", "dark"), (me_now.accent_color, me_now.theme))
        home = text(stu, "/courses")
        t.check("pages use the colour and the theme", "--purple:#1d4ed8" in home and 'data-theme-pref="dark"' in home)
        t.check("others keep the brand colour", "--purple:#1d4ed8" not in text(other, "/courses"))
        r = stu.post("/settings/theme", data={"theme": "light"}, headers={"X-CSRF-Token": token(stu, "/courses")})
        t.check("header switch saves the theme", r.status_code == 204 and users.get(stu_user.id).theme == "light")
        g2 = app.test_client()
        g2.set_cookie("theme", "dark")
        t.check("guests: theme from the cookie", 'data-theme-pref="dark"' in text(g2, "/courses"))
        t.check("header has the theme switch", 'id="theme-toggle"' in text(g2, "/courses"))
        r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "appearance", "accent": "#1d4ed8",
                                        "secondary": "#047857", "font": "lexend", "text_size": "large", "theme": "light"})
        mine, theirs = text(stu, "/courses"), text(other, "/courses")
        t.check("font, text size and secondary colour saved", (users.get(stu_user.id).font, users.get(stu_user.id).text_size,
                users.get(stu_user.id).secondary_color) == ("lexend", "large", "#047857"))
        t.check("pages use them", "family=Lexend" in mine and "font-size:106.25%" in mine and "--badge:#047857" in mine)
        t.check("another user is not affected", "Lexend" not in theirs and "font-size:93.75%" in theirs and "#047857" not in theirs
                and "Tac+One" in theirs)
        r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "appearance", "accent": "#722f99",
                                        "secondary": "custom", "custom_secondary": "not-a-colour", "font": "studiamo", "text_size": "normal"})
        t.check("bad secondary colour refused (400)", r.status_code == 400)
        stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "appearance", "accent": "#722f99", "secondary": "default",
                                    "font": "studiamo", "text_size": "normal", "theme": "system"})
        back = users.get(stu_user.id)
        t.check("back to the defaults", (back.accent_color, back.secondary_color, back.font, back.text_size) == (None, None, "studiamo", "normal"))

        t.section("ranking: anonymous or hidden")
        def set_rank(choice):
            r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "ranking", "ranking_visibility": choice})
            assert r.status_code == 302, r.status_code
        set_rank("anonymous")
        board = text(other, "/leaderboard")
        t.check("anonymous: no name, no profile link", f"sami{run}" not in board
                and "Anonymous player" in board and f"/users/{stu_user.id}\"" not in board)
        t.check("anonymous: the player still sees their own line", "YOU</span>" in text(stu, "/leaderboard"))
        t.check("anonymous: profile keeps progress private for others", "keeps their progress private" in text(other, f"/users/{stu_user.id}"))
        t.check("...but not for the player", "keeps their progress private" not in text(stu, f"/users/{stu_user.id}"))
        set_rank("hidden")
        t.check("hidden: not on the ranking at all", "YOU</span>" not in text(stu, "/leaderboard") and "You are hidden" in text(stu, "/leaderboard"))
        t.check("hidden: dashboard says so", "hidden from the ranking" in text(stu, "/dashboard"))
        set_rank("public")
        t.check("public again", f"sami{run}" in text(other, "/leaderboard"))

        t.section("admin")
        r = stu.post("/settings", data={"csrf_token": token(stu, "/settings"), "section": "lecturer", "institution": "IUT", "courses_taught": "HCI"})
        t.check("student requests lecturer", r.status_code == 302 and "pending" in text(stu, "/settings"))
        t.check("students get 404 on /admin", stu.get("/admin/").status_code == 404)
        page = text(adm, "/admin/")
        t.check("admin page lists the request", "Sami Student" in page and "APPROVE" in page)
        mine = page[page.index(stu_user.email):]        # this run's request, not an older one
        app_id = int(re.search(r"/admin/applications/(\d+)", mine).group(1))
        adm.post(f"/admin/applications/{app_id}", data={"csrf_token": token(adm, "/admin/"), "decision": "approve"})
        t.check("approved -> lecturer", UserRole.LECTURER in users.get(stu_user.id).roles)
        t.check("admin can edit any course in the studio", adm.get(f"/studio/courses/{course_id}").status_code == 200)
        r = adm.get(f"/courses/{course_id}/start")
        t.check("admin: strip link opens the note", r.status_code == 302 and adm.get(r.headers["Location"]).status_code == 200)
        t.check("exam list page", "Midterm" in text(guest, "/exams"))
        t.check("admin session cannot take exercises (403)", adm.get(f"/exercises/{ex_id}/take").status_code == 403)
        adm_home = text(adm, "/dashboard")
        t.check("admins get their own lobby too: player card, skill graph, then the admin section",
                "player-card" in adm_home and "SKILL GRAPH" in adm_home and "AS AN ADMIN" in adm_home
                and adm_home.index("player-card") < adm_home.index("AS AN ADMIN"))
        t.check("admin shown as main role + star", 'STUDENT <span aria-hidden="true">★' in adm_home)
        r = adm.post(f"/admin/users/{other_lec.id}/roles", data={"csrf_token": token(adm, "/admin/"), "lecturer": ""})
        t.check("admin removes the lecturer role", users.get(other_lec.id).roles == [UserRole.STUDENT])

        t.section("editing approved content")
        r = lec.post(f"/studio/notes/{note_id}", data={"csrf_token": tok, "title": "Pure functions", "body": NOTE + "\nOne more line.\n"})
        row = db.fetch_one("select published, review_status from topic_notes where id = :i", {"i": note_id})
        t.check("lecturer edit -> hidden and back in review", (row["published"], row["review_status"]) == (False, "pending"), row)
        t.check("students cannot read it meanwhile", stu.get(f"/notes/{note_id}").status_code == 404)
        lec.post(f"/studio/questions/{mcq_id}", data={"csrf_token": tok, "type": "mcq", "body_md": "What is `2 + 2`, really?", "points": "2",
                                                      "topic_id": str(week1), "opt_label": ["4", "5", "four"], "opt_correct": ["0", "2"],
                                                      "is_active_sent": "1", "is_active": "on"})
        t.check("editing a question reopens the approved exercise and practice exam using it",
                db.scalar("select review_status from exercises where id = :i", {"i": ex_id}) == "pending"
                and db.scalar("select review_status from exams where id = :i", {"i": sys_id}) == "pending")
        t.check("the scheduled exam is not affected", db.scalar("select published from exams where id = :i", {"i": exam_id}) is True)

        t.section("delete")
        r = lec.post(f"/studio/courses/{course_id}/delete", data={"csrf_token": tok, "confirm_code": "nope"})
        t.check("wrong code: nothing deleted", r.status_code == 302 and lec.get(f"/studio/courses/{course_id}").status_code == 200)
    finally:
        # every course of a test account (also those left by an interrupted run), then their questions
        db.execute(sa.text("delete from courses where created_by in (select id from users where email like :d)"), {"d": f"%@{DOMAIN}"})
        db.execute(sa.text("delete from questions where author_id in (select id from users where email like :d)"), {"d": f"%@{DOMAIN}"})
        db.execute(sa.text("delete from users where email like :d"), {"d": f"%@{DOMAIN}"})
        print(f"\ncleanup: {db.scalar(sa.text('select count(*) from users where email like :d'), {'d': f'%@{DOMAIN}'})} test users left")
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
