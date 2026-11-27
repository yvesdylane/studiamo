"""The exam guard's server side: the exam page reports what it saw (left the tab,
left full screen, went back…), can submit the exam by itself, and the lecturer
sees it on the Results page. The browser part is static/js/exam-guard.js.

    uv run python -m tests.test_exam_guard

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
    from src.user.service.UserService import UserService
    from src.web.studio_tests import to_local

    app = create_app()
    mailer = FakeMailer()
    app.extensions["mailer"] = mailer
    db = app.extensions["db"]
    users = UserService(db)
    t = Checks()
    run = uuid.uuid4().hex[:6]
    pw = "correct-horse-9"

    def account(name, lecturer=False):
        u = users.create(UserCreate(name=name, email=f"{name.lower().replace(' ', '')}-{run}@{DOMAIN}", password=pw, accepted_terms=True))
        users.mark_email_verified(u.id, u.email)
        if lecturer:
            users.set_roles(u.id, UserRolesUpdate(roles=[UserRole.STUDENT, UserRole.LECTURER]))
        return users.get(u.id)

    def login(user):
        c = app.test_client()
        c.post("/login", data={"csrf_token": token(c, "/login"), "email": user.email, "password": pw})
        return c

    def attempt_row(aid):
        return db.fetch_all("select status, auto_submitted, integrity_events from exam_attempts where id = :a", {"a": aid})[0]

    try:
        lec, stu1, stu2 = login(account("Gus Lecturer", True)), account("Ana One"), account("Ben Two")
        s1, s2 = login(stu1), login(stu2)
        tok = token(lec, "/studio/")
        lec.post("/studio/questions/new", data={"csrf_token": tok, "type": "mcq", "body_md": f"1 + 1 = ? {run}", "points": "1",
                                                "opt_label": ["2", "3"], "opt_correct": ["0"]})
        qid = db.fetch_all("select id from questions where body_md = :b", {"b": f"1 + 1 = ? {run}"})[0]["id"]
        now = datetime.now(UTC)
        r = lec.post("/studio/exams/new", data={"csrf_token": tok, "kind": "scheduled", "title": f"Guarded {run}",
                                                "opens_at": to_local(now - timedelta(hours=1)), "closes_at": to_local(now + timedelta(hours=3)),
                                                "duration_minutes": "30", "question_ids": [str(qid)]})
        exam_id = int(re.search(r"/studio/exams/(\d+)", r.headers["Location"]).group(1))
        lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "publish"})
        lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "join", "join_enabled": "on",
                                                   "join_mode": "anyone", "guest_results": "graded"})
        code = db.fetch_all("select join_code from exams where id = :x", {"x": exam_id})[0]["join_code"]

        def start(c):
            c.post("/join", data={"csrf_token": token(c, "/join"), "code": code})
            r = c.post(f"/exams/{exam_id}/start", data={"csrf_token": token(c, f"/exams/{exam_id}")})
            return int(re.search(r"/exams/attempts/(\d+)", r.headers["Location"]).group(1))

        t.section("the exam page")
        a1 = start(s1)
        page = s1.get(f"/exams/attempts/{a1}").get_data(as_text=True)
        t.check("starts behind the full-screen gate, with the rules", 'id="guard-gate"' in page and "START IN FULL SCREEN" in page
                and "submitted automatically" in page)
        t.check("the guard script and its event address are on the page", "exam-guard.js" in page and f'data-guard-url="/exams/attempts/{a1}/event"' in page)
        t.check("no 'Leave (the attempt stays open)' link in an exam", "the attempt stays open" not in page)

        t.section("events")
        ev = f"/exams/attempts/{a1}/event"
        r = s1.post(ev, data={"csrf_token": token(s1, f"/exams/attempts/{a1}"), "event": "started_fullscreen"})
        t.check("event recorded", r.status_code == 200 and r.get_json()["events"] == 1)
        ctok = token(s1, f"/exams/attempts/{a1}")
        for e in ("left_fullscreen", "left_window", "copy"):
            s1.post(ev, data={"csrf_token": ctok, "event": e})
        t.check("unknown event -> 400", s1.post(ev, data={"csrf_token": ctok, "event": "hack"}).status_code == 400)
        t.check("without the CSRF token -> refused", s1.post(ev, data={"event": "left_window"}).status_code in (400, 403))
        t.check("another student cannot write to it (404)", s2.post(ev, data={"csrf_token": token(s2, "/courses"), "event": "left_window"}).status_code == 404)
        types = [e["type"] for e in attempt_row(a1)["integrity_events"]]
        t.check("events kept in order with a time", types == ["started_fullscreen", "left_fullscreen", "left_window", "copy"]
                and all("at" in e for e in attempt_row(a1)["integrity_events"]))

        t.section("submitted by the guard")
        r = s1.post(f"/exams/attempts/{a1}", data={"csrf_token": ctok, "auto_submit": "left"})
        row = attempt_row(a1)
        t.check("auto-submit 'left' -> graded, reason kept, logged", r.status_code == 302 and row["status"] == "graded"
                and row["auto_submitted"] == "left" and row["integrity_events"][-1]["type"] == "auto_submit_left")
        t.check("the student is told why", "because you left it again" in s1.get(r.headers["Location"]).get_data(as_text=True))
        t.check("events after submitting -> 409", s1.post(ev, data={"csrf_token": ctok, "event": "left_window"}).status_code == 409)
        a2 = start(s2)
        r = s2.post(f"/exams/attempts/{a2}", data={"csrf_token": token(s2, f"/exams/attempts/{a2}"), "auto_submit": "whatever"})
        t.check("an unknown reason is ignored (normal submit)", attempt_row(a2)["auto_submitted"] is None and attempt_row(a2)["status"] == "graded")

        t.section("guest")
        g = app.test_client()
        gmail = f"gina-{run}@{DOMAIN}"
        g.post("/join", data={"csrf_token": token(g, "/join"), "code": code, "name": "Gina Guest", "email": gmail})
        g.post("/join/verify", data={"csrf_token": token(g, "/join/verify"), "code": CODE_RE.search(mailer.to(gmail)[-1].text).group(1)})
        r = g.post("/join/start", data={"csrf_token": token(g, "/join/exam")})
        ga = int(re.search(r"/join/attempt/(\d+)", r.headers["Location"]).group(1))
        page = g.get(f"/join/attempt/{ga}").get_data(as_text=True)
        t.check("guests get the guard too", 'id="guard-gate"' in page and f'data-guard-url="/join/attempt/{ga}/event"' in page)
        gtok = token(g, f"/join/attempt/{ga}")
        t.check("guest event recorded", g.post(f"/join/attempt/{ga}/event", data={"csrf_token": gtok, "event": "back"}).get_json()["events"] == 1)
        t.check("a stranger cannot write to a guest's attempt", app.test_client().post(f"/join/attempt/{ga}/event", data={"event": "back"}).status_code in (400, 403, 404))
        r = g.post(f"/join/attempt/{ga}", data={"csrf_token": gtok, "auto_submit": "back"})
        t.check("guest auto-submit 'back' kept", attempt_row(ga)["auto_submitted"] == "back")
        t.check("done page says why", "because you chose to leave" in g.get("/join/done").get_data(as_text=True))

        t.section("the lecturer sees it")
        page = lec.get(f"/studio/exams/{exam_id}/results").get_data(as_text=True)
        t.check("DURING THE EXAM column", "DURING THE EXAM" in page)
        t.check("auto-submitted attempt flagged with what happened", "CHECK" in page and "submitted automatically after leaving" in page
                and "left the exam" in page and "left full screen" in page and "tried to copy" in page)
        t.check("guest who left via Back", "chose to leave (submitted)" in page and "tried to go back" in page)
        t.check("an attempt without events says so", "nothing recorded" in page)
        t.check("timeline available on hover", re.search(r'title="\d\d:\d\d:\d\d  started fullscreen', page) is not None)

        t.section("only the exam itself is guarded")
        page = s1.get(f"/exams/{exam_id}").get_data(as_text=True)
        t.check("exam lobby has no gate (only the exam itself)", 'id="guard-gate"' not in page)
    finally:
        d = {"d": f"%@{DOMAIN}"}
        db.execute(sa.text("delete from exams where created_by in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from questions where author_id in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from users where email like :d"), d)
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
