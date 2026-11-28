"""The MCP exam tools: create_exam and list_exams.

    uv run python -m tests.test_mcp_exams

Runs against the real database in .env; accounts use @studiamo-test.example and
are deleted at the end. Do not run it at the same time as another web test.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from tests.harness import Checks
from tests.test_web_auth import DOMAIN, FakeMailer, token

CODE_RE = re.compile(r"\b(\d{6})\b")


def main() -> int:
    os.environ["MARKDOWN_DIR"] = tempfile.mkdtemp(prefix="studiamo-md-")
    from src.app import create_app
    from src.user.dto.UserDto import UserCreate, UserRolesUpdate
    from src.user.models.User import UserRole
    from src.user.service.ApiTokenService import ApiTokenService
    from src.user.service.UserService import UserService

    app = create_app()
    mailer = FakeMailer()
    app.extensions["mailer"] = mailer
    db = app.extensions["db"]
    users = UserService(db)
    t = Checks()
    run = uuid.uuid4().hex[:6]

    def account(name, lecturer=False):
        u = users.create(UserCreate(name=name, email=f"{name.lower().replace(' ', '')}-{run}@{DOMAIN}", password="correct-horse-9", accepted_terms=True))
        users.mark_email_verified(u.id, u.email)
        if lecturer:
            users.set_roles(u.id, UserRolesUpdate(roles=[UserRole.STUDENT, UserRole.LECTURER]))
        return users.get(u.id)

    client = app.test_client()
    ids = iter(range(1, 1000))

    def call(key, tool, args):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": next(ids), "method": "tools/call",
                                      "params": {"name": tool, "arguments": args}},
                        headers={"Authorization": f"Bearer {key}"})
        res = r.get_json()["result"]
        return res.get("isError", False), res["content"][0]["text"]

    import json

    def mcq(n, correct=(1,)):
        return {"question": f"Q{n} {run}: pick B", "options": ["A", "B", "C"], "correct": list(correct), "explanation": "B is right"}

    try:
        lec = account("Mia Lecturer", True)
        other = account("Oli Other", True)
        key, _unused = ApiTokenService(db).create(lec.id, "test")
        okey, _unused = ApiTokenService(db).create(other.id, "test")

        t.section("the tools are listed")
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 0, "method": "tools/list"}, headers={"Authorization": f"Bearer {key}"})
        names = [x["name"] for x in r.get_json()["result"]["tools"]]
        t.check("create_exam and list_exams", "create_exam" in names and "list_exams" in names)

        t.section("bad input is refused, nothing saved")
        before = db.fetch_all("select count(*) n from questions where author_id = :u", {"u": lec.id})[0]["n"]
        err, text = call(key, "create_exam", {"title": "Bad", "questions": [mcq(1), {"question": "x", "options": ["a", "b"], "correct": [5]}]})
        t.check("correct out of range -> error", err and "Question 2" in text)
        err, text = call(key, "create_exam", {"title": "Bad", "questions": [mcq(1)], "opens_at": "tomorrow"})
        t.check("unreadable date -> error", err and "ISO 8601" in text)
        err, _unused = call(key, "create_exam", {"title": "Bad", "questions": [mcq(1)], "opens_at": "2020-01-01T10:00", "closes_at": "2020-01-01T12:00"})
        t.check("already closed -> error", err)
        err, _unused = call(key, "create_exam", {"title": "Bad", "questions": [mcq(1)], "join": "off"})
        t.check("no course and no code -> error", err)
        err, _unused = call(key, "create_exam", {"title": "Bad", "questions": [mcq(1)], "join": "list"})
        t.check("list without emails -> error", err)
        err, _unused = call(okey, "create_exam", {"title": "Bad", "questions": [mcq(1)], "course_id": 999999999})
        t.check("someone else's / unknown course -> error", err)
        t.check("no question was saved by the refused calls",
                db.fetch_all("select count(*) n from questions where author_id = :u", {"u": lec.id})[0]["n"] == before)

        t.section("an exam that opens in 5 minutes")
        err, text = call(key, "create_exam", {"title": f"MCP exam {run}", "questions": [mcq(i) for i in range(1, 11)],
                                              "opens_in_minutes": 5, "duration_minutes": 20})
        t.check("created", not err)
        info = json.loads(text)
        exam_id = info["exam_id"]
        row = db.fetch_all("select * from exams where id = :x", {"x": exam_id})[0]
        delta = (row["opens_at"] - datetime.now(UTC)).total_seconds()
        t.check("opens in about 5 minutes", 200 < delta < 310)
        t.check("closes 2 hours after opening by default", row["closes_at"] - row["opens_at"] == timedelta(hours=2))
        t.check("published, scheduled, no course, one attempt", row["published"] and row["kind"] == "scheduled"
                and row["course_id"] is None and row["max_attempts"] == 1 and row["duration_minutes"] == 20)
        t.check("join code on, anyone, graded results", row["join_enabled"] and row["join_mode"] == "anyone"
                and row["guest_results"] == "graded" and info["join_code"] and "-" in info["join_code"])
        qs = db.fetch_all("select q.id, q.author_id, q.explanation_md from exam_questions eq join questions q on q.id = eq.question_id where eq.exam_id = :x", {"x": exam_id})
        t.check("10 questions, written by the lecturer, with explanations", len(qs) == 10
                and all(q["author_id"] == lec.id and q["explanation_md"] == "B is right" for q in qs))
        opts = db.fetch_all("select label, is_correct from question_options where question_id = :q order by id", {"q": qs[0]["id"]})
        t.check("the right option is marked correct", [o["is_correct"] for o in opts] == [False, True, False])
        t.check("links to the Studio page", info["studio_url"].endswith(f"/studio/exams/{exam_id}"))

        t.section("a guest can find it by its code")
        from src.exam.service.ExamService import ExamService
        found = ExamService(db).find_by_code(info["join_code"])
        t.check("the code finds the exam", found is not None and found.id == exam_id)
        g = app.test_client()
        r = g.post("/join", data={"csrf_token": token(g, "/join"), "code": info["join_code"], "name": "Gil Guest", "email": f"gil-{run}@{DOMAIN}"})
        t.check("a guest gets an email code", r.status_code == 302 and CODE_RE.search(mailer.to(f"gil-{run}@{DOMAIN}")[-1].text))

        t.section("more options")
        err, text = call(key, "create_exam", {"title": f"Listed {run}", "questions": [mcq(1, (0, 1))],
                                              "opens_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                                              "open_for_minutes": 60, "join": "list", "allowed_emails": ["a@x.org"],
                                              "publish": False, "shuffle": False})
        info2 = json.loads(text)
        row = db.fetch_all("select * from exams where id = :x", {"x": info2["exam_id"]})[0]
        t.check("draft, class list, no shuffling, 1 hour", not err and not row["published"] and row["join_mode"] == "list"
                and not row["shuffle_questions"] and row["closes_at"] - row["opens_at"] == timedelta(hours=1))
        t.check("two correct options -> allow_multiple", db.fetch_all("select allow_multiple from questions where id = :q",
                                                                    {"q": info2["question_ids"][0]})[0]["allow_multiple"])

        t.section("list_exams")
        err, text = call(key, "list_exams", {})
        listed = {e["exam_id"]: e for e in json.loads(text)}
        t.check("both exams, with question counts", not err and listed[exam_id]["questions"] == 10 and info2["exam_id"] in listed)
        err, text = call(okey, "list_exams", {})
        t.check("another lecturer does not see them", text == "You have no exams yet.")
    finally:
        d = {"d": f"%@{DOMAIN}"}
        db.execute(sa.text("delete from exams where created_by in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from questions where author_id in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from users where email like :d"), d)
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
