"""Exams on their own, joined with a code: by logged-in students and by guests
(name + email checked with an emailed 6-digit code), with results emailed to
guests as soon as they are graded or when the lecturer sends them.

    uv run python -m tests.test_join_exam

Runs against the real database in .env. Accounts use @studiamo-test.example
and are deleted at the end with everything they made (guests go with the exam).
Do not run it at the same time as another web test: they share that cleanup.
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


def text(client, path: str) -> str:
    return client.get(path).get_data(as_text=True)


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

    def correct_option(qid: int) -> int:
        return db.fetch_all("select id from question_options where question_id = :q and is_correct", {"q": qid})[0]["id"]

    def guest_join(code: str, name: str, email: str):
        """A fresh browser joins as a guest and checks its email; returns (client, response)."""
        c = app.test_client()
        r = c.post("/join", data={"csrf_token": token(c, "/join"), "code": code, "name": name, "email": email})
        if r.status_code != 302:
            return c, r
        mail = mailer.to(email)[-1]
        r = c.post("/join/verify", data={"csrf_token": token(c, "/join/verify"), "code": CODE_RE.search(mail.text).group(1)})
        return c, r

    def take(c, qid: int, right: bool = True):
        tok = token(c, "/join/exam")
        r = c.post("/join/start", data={"csrf_token": tok})
        attempt_id = int(re.search(r"/join/attempt/(\d+)", r.headers["Location"]).group(1))
        page = text(c, f"/join/attempt/{attempt_id}")
        options = [int(x) for x in re.findall(rf'name="q{qid}" value="(\d+)"', page)]
        good = correct_option(qid)
        pick = good if right else next(o for o in options if o != good)
        r = c.post(f"/join/attempt/{attempt_id}", data={"csrf_token": token(c, f"/join/attempt/{attempt_id}"), f"q{qid}": str(pick)})
        return attempt_id, r

    try:
        lec_user, other_user, stu_user = account("Jo Lecturer", lecturer=True), account("Ola Other", lecturer=True), account("Sue Student")
        lec, other, stu = login(lec_user), login(other_user), login(stu_user)

        t.section("a question and an exam without a course")
        tok = token(lec, "/studio/")
        page = text(lec, "/studio/exams/new")
        t.check("new exam page offers + NEW question links", "add_to=new-exam" in page)
        r = lec.post("/studio/questions/new", data={"csrf_token": tok, "type": "mcq", "body_md": f"2 + 2 = ? {run}", "points": "2",
                                                    "opt_label": ["4", "5"], "opt_correct": ["0"], "add_to": "new-exam", "topic_id": "1"})
        t.check("question saved -> back to the new exam", r.status_code == 302 and r.headers["Location"].endswith("/studio/exams/new"), r.status_code)
        qid = db.fetch_all("select id, topic_id from questions where body_md = :b", {"b": f"2 + 2 = ? {run}"})[0]
        t.check("the question has no topic (a topic id sent by the form is ignored)", qid["topic_id"] is None)
        qid = qid["id"]
        t.check("its author can open it", lec.get(f"/studio/questions/{qid}").status_code == 200)
        t.check("another lecturer cannot (404)", other.get(f"/studio/questions/{qid}").status_code == 404)

        now = datetime.now(UTC)
        r = lec.post("/studio/exams/new", data={"csrf_token": tok, "kind": "scheduled", "title": f"Quiz {run}",
                                                "opens_at": to_local(now - timedelta(hours=1)), "closes_at": to_local(now + timedelta(hours=3)),
                                                "duration_minutes": "30", "question_ids": [str(qid)], "shuffle_options": "on"})
        t.check("exam created -> its editor", r.status_code == 302 and "/studio/exams/" in r.headers["Location"], r.get_data(as_text=True)[:300])
        exam_id = int(re.search(r"/studio/exams/(\d+)", r.headers["Location"]).group(1))
        row = db.fetch_all("select course_id, join_code, join_enabled from exams where id = :x", {"x": exam_id})[0]
        t.check("no course, no code yet, code off", row["course_id"] is None and row["join_code"] is None and not row["join_enabled"])
        t.check("listed in the Studio under exams without a course", f"Quiz {run}" in text(lec, "/studio/"))
        t.check("another lecturer cannot open it (404)", other.get(f"/studio/exams/{exam_id}").status_code == 404)

        lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "publish"})
        r = lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "join", "join_enabled": "on",
                                                       "join_mode": "anyone", "guest_results": "graded"})
        code = db.fetch_all("select join_code from exams where id = :x", {"x": exam_id})[0]["join_code"]
        t.check("switching joining on makes a 6-character code", r.status_code == 302 and code and len(code) == 6, code)
        shown = f"{code[:3]}-{code[3:]}"
        t.check("the editor shows the code as ABC-123", shown in text(lec, f"/studio/exams/{exam_id}"))
        t.check("lecturer's lobby shows the code", shown in text(lec, "/dashboard"))

        t.section("not reachable without the code")
        t.check("student opening the exam page directly: 404", stu.get(f"/exams/{exam_id}").status_code == 404)
        r = stu.post(f"/exams/{exam_id}/start", data={"csrf_token": token(stu, "/join")})
        t.check("starting it directly sends them to Join", r.status_code == 302 and "/join" in r.headers["Location"])
        t.check("anonymous visitor: 404", app.test_client().get(f"/exams/{exam_id}").status_code == 404)
        t.check("JOIN AN EXAM in the header", "JOIN AN EXAM" in text(app.test_client(), "/"))

        t.section("guest: code, email check, exam, result email")
        g1 = f"guest1-{run}@{DOMAIN}"
        c = app.test_client()
        r = c.post("/join", data={"csrf_token": token(c, "/join"), "code": "ZZZ-ZZZ", "name": "Gil", "email": g1})
        t.check("wrong code -> 400 with a message", r.status_code == 400 and "does not work" in r.get_data(as_text=True))
        r = c.post("/join", data={"csrf_token": token(c, "/join"), "code": shown.lower(), "name": "", "email": g1})
        t.check("missing name -> 400", r.status_code == 400 and "name" in r.get_data(as_text=True))
        r = c.post("/join", data={"csrf_token": token(c, "/join"), "code": shown.lower(), "name": "Gil Guest", "email": g1})
        t.check("right code (any case, with dash) -> check your email", r.status_code == 302 and r.headers["Location"].endswith("/join/verify"))
        t.check("a 6-digit code was emailed", len(mailer.to(g1)) == 1 and CODE_RE.search(mailer.to(g1)[0].text))
        sent_code = CODE_RE.search(mailer.to(g1)[0].text).group(1)
        r = c.post("/join/resend", data={"csrf_token": token(c, "/join/verify")})
        t.check("asking again within a minute sends nothing", len(mailer.to(g1)) == 1)
        wrong = "000000" if sent_code != "000000" else "111111"
        r = c.post("/join/verify", data={"csrf_token": token(c, "/join/verify"), "code": wrong})
        t.check("wrong 6-digit code -> 400, tries left", r.status_code == 400 and "4 tries left" in r.get_data(as_text=True))
        t.check("the exam is not open before the check", c.get("/join/exam").headers.get("Location", "").endswith("/join"))
        r = c.post("/join/verify", data={"csrf_token": token(c, "/join/verify"), "code": sent_code})
        t.check("right code -> the exam's page", r.status_code == 302 and r.headers["Location"].endswith("/join/exam"))
        t.check("guest page greets them", "Gil Guest" in text(c, "/join/exam"))
        attempt_id, r = take(c, qid, right=True)
        t.check("submitted -> done page", r.status_code == 302 and r.headers["Location"].endswith("/join/done"))
        t.check("done page says the result is emailed", "will be emailed" in text(c, "/join/done"))
        a = db.fetch_all("select student_id, guest_id, status, score_pct from exam_attempts where id = :a", {"a": attempt_id})[0]
        t.check("the attempt belongs to the guest, graded 100%", a["student_id"] is None and a["guest_id"] and a["status"] == "graded" and float(a["score_pct"]) == 100, a)
        t.check("result emailed at once (graded mode)", len(mailer.to(g1)) == 2 and "100%" in mailer.to(g1)[1].text)
        t.check("a second attempt is refused (one attempt)", "already taken" in text(c, "/join/exam"))
        t.check("the guest cannot open another guest's or student's pages", c.get("/results/exam/%d" % attempt_id).status_code in (302, 401, 404))

        t.section("logged-in student joins with the code")
        r = stu.post("/join", data={"csrf_token": token(stu, "/join"), "code": code})
        t.check("-> the normal exam page", r.status_code == 302 and r.headers["Location"].endswith(f"/exams/{exam_id}"))
        t.check("the exam page opens now", stu.get(f"/exams/{exam_id}").status_code == 200)
        r = stu.post(f"/exams/{exam_id}/start", data={"csrf_token": token(stu, f"/exams/{exam_id}")})
        sid = int(re.search(r"/exams/attempts/(\d+)", r.headers["Location"]).group(1))
        r = stu.post(f"/exams/attempts/{sid}", data={"csrf_token": token(stu, f"/exams/attempts/{sid}"), f"q{qid}": str(correct_option(qid))})
        a = db.fetch_all("select student_id, status from exam_attempts where id = :a", {"a": sid})[0]
        t.check("their attempt is theirs and graded", a["student_id"] == stu_user.id and a["status"] == "graded")

        t.section("class list and sending results by hand")
        g2, g3 = f"guest2-{run}@{DOMAIN}", f"guest3-{run}@{DOMAIN}"
        lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "join", "join_enabled": "on", "join_mode": "list",
                                                   "guest_results": "manual", "allowed_emails": f"{g2.upper()}, {stu_user.email}"})
        t.check("class list saved (lower-cased)", sorted(r["email"] for r in db.fetch_all(
            "select email from exam_allowed_emails where exam_id = :x", {"x": exam_id})) == sorted([g2, stu_user.email]))
        c3, r = guest_join(code, "Not Listed", g3)
        t.check("email not on the list -> 400, no email sent", r.status_code == 400 and "class list" in r.get_data(as_text=True) and not mailer.to(g3))
        other_stu = login(account("Nia Notlisted"))
        r = other_stu.post("/join", data={"csrf_token": token(other_stu, "/join"), "code": code})
        t.check("logged-in student not on the list -> 400", r.status_code == 400)
        c2, r = guest_join(code, "Lia Listed", g2)
        t.check("listed guest gets in", r.status_code == 302 and r.headers["Location"].endswith("/join/exam"))
        attempt2, r = take(c2, qid, right=False)
        t.check("manual mode: no result email yet", len(mailer.to(g2)) == 1)
        t.check("done page: sent when the lecturer sends them", "when your lecturer sends" in text(c2, "/join/done"))
        page = text(lec, f"/studio/exams/{exam_id}/results")
        t.check("results page lists guests, with the email button (1 waiting)", "GUEST" in page and "EMAIL RESULTS TO GUESTS (1)" in page)
        r = lec.post(f"/studio/exams/{exam_id}/email-results", data={"csrf_token": tok})
        t.check("button emails the waiting guest", r.status_code == 302 and len(mailer.to(g2)) == 2 and "0%" in mailer.to(g2)[1].text)
        lec.post(f"/studio/exams/{exam_id}/email-results", data={"csrf_token": tok})
        t.check("pressing again sends nothing more", len(mailer.to(g2)) == 2 and len(mailer.to(g1)) == 2)
        t.check("another lecturer cannot send them (404)", other.post(f"/studio/exams/{exam_id}/email-results", data={"csrf_token": token(other, "/studio/")}).status_code == 404)

        t.section("new code, code off")
        lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "new_code"})
        new = db.fetch_all("select join_code from exams where id = :x", {"x": exam_id})[0]["join_code"]
        t.check("new code differs", new and new != code)
        _, r = guest_join(code, "Late Comer", f"guest4-{run}@{DOMAIN}")
        t.check("old code no longer works", r.status_code == 400)
        lec.post(f"/studio/exams/{exam_id}", data={"csrf_token": tok, "action": "join", "join_mode": "anyone", "guest_results": "graded", "allowed_emails": ""})
        _, r = guest_join(new, "Late Comer", f"guest4-{run}@{DOMAIN}")
        t.check("code switched off -> refused", r.status_code == 400)
    finally:
        d = {"d": f"%@{DOMAIN}"}
        db.execute(sa.text("delete from exams where created_by in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from courses where created_by in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from questions where author_id in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from users where email like :d"), d)
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
