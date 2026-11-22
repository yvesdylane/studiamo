"""Checks the skill graph and reading time, against the real database in .env.

    uv run python -m tests.test_skill_graph

  reading   time on each note / (3 x its estimated reading time), capped at 100%
  practice  average best exercise score; exams: average best exam score
  subjects  average completion of the courses started
Everything it creates is deleted at the end.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid

import sqlalchemy as sa

from tests.harness import Checks

DOMAIN = "studiamo-test.example"


def main() -> int:
    os.environ["MARKDOWN_DIR"] = tempfile.mkdtemp(prefix="studiamo-md-")
    os.environ["MEDIA_DIR"] = tempfile.mkdtemp(prefix="studiamo-media-")
    from src.app import create_app
    from src.course.dto.CourseDto import CourseCreate
    from src.course.dto.NoteDto import NoteCreate, NoteUpdate
    from src.course.dto.TopicDto import TopicCreate
    from src.user.dto.UserDto import UserCreate, UserRolesUpdate
    from src.user.models.User import UserRole
    from src.web import deps
    from src.web.security import SESSION_USER_KEY

    app = create_app()
    db = app.extensions["db"]
    t = Checks()
    run = uuid.uuid4().hex[:6]
    with app.test_request_context():
        users, courses, topics, notes, progress = deps.users(), deps.courses(), deps.topics(), deps.notes(), deps.progress()
    try:
        lec = users.create(UserCreate(name="Sk Lecturer", email=f"sklec-{run}@{DOMAIN}", password="longenough1"))
        users.set_roles(lec.id, UserRolesUpdate(roles=[UserRole.STUDENT, UserRole.LECTURER]))
        stu = users.create(UserCreate(name="Sk Student", email=f"skstu-{run}@{DOMAIN}", password="longenough1"))
        for u in (lec, stu):
            users.mark_email_verified(u.id, u.email)
        course = courses.create(CourseCreate(code=f"SK{run}", title="Skills"), created_by=lec.id)
        topic = topics.create(TopicCreate(course_id=course.id, title="T1", position=0))
        n1 = notes.create(NoteCreate(topic_id=topic.id, title="Short", md_path=f"notes/sk/{run}a.md", position=0, max_exp=10))
        n2 = notes.create(NoteCreate(topic_id=topic.id, title="Other", md_path=f"notes/sk/{run}b.md", position=1, max_exp=10))
        saved = notes.save_content(n1.id, "# Short\n\n" + "word " * 400)      # 400 words = 2 min
        notes.save_content(n2.id, "# Other\n\nHi.")
        for n in (n1, n2):
            notes.update(n.id, NoteUpdate(published=True))

        from src.progress.service import Engagement
        day = Engagement.today()
        for key, target in (("note_1", 1), ("read_10", 600), ("exercise_1", 1)):   # known tasks for today
            db.execute(sa.text("insert into student_daily_tasks (student_id, day, task_key, target) values (:s, :d, :k, :t)"),
                       {"s": stu.id, "d": day, "k": key, "t": target})

        t.section("estimated reading time")
        t.check("saved with the note: 400 words -> 120 s", saved.reading_seconds == 120, saved.reading_seconds)

        t.section("nothing done yet")
        g = progress.skill_graph(stu.id)
        t.check("every axis 'not started'", [g[k] for k in ("reading", "practice", "exams", "subjects")] == [None] * 4, g)
        t.check("streak, time and badges at 0", (g["streak"], g["time"], g["badges"]) == (0.0, 0.0, 0.0), g)

        t.section("reading time is capped")
        c = app.test_client()
        with c.session_transaction() as s:
            s[SESSION_USER_KEY] = stu.id
        page = c.get(f"/notes/{n1.id}").get_data(as_text=True)
        t.check("note page has the tracker", 'id="reading-time"' in page and "reading-time.js" in page and "About 2 min to read" in page)
        import re
        tok = re.search(r'data-csrf="([^"]+)"', page).group(1)
        import time
        start = time.monotonic()
        for _ in range(3):   # three reports of 60 s each, sent one right after the other
            c.post(f"/notes/{n1.id}/time", data={"seconds": "60", "csrf_token": tok})
        elapsed = time.monotonic() - start
        secs = db.scalar(sa.text("select seconds from student_note_time where student_id = :s and note_id = :n"), {"s": stu.id, "n": n1.id})
        # the first report counts (<= 60 s); the next ones only up to the real time since the previous one (+2 s each)
        t.check("burst of reports: only real time counts", secs is not None and 60 <= secs <= 60 + elapsed + 5 and secs < 180,
                (secs, round(elapsed, 1)))
        t.check("more than 60 s in one report is cut", progress.add_reading_time(stu.id, n2.id, 5000) == 60)
        t.check("no CSRF token -> refused", c.post(f"/notes/{n1.id}/time", data={"seconds": "60"}).status_code == 400)

        t.section("the four axes")
        # 180 s on note 1 (estimate 120 s -> needs 360 s): 50%; note 2: 60 s of 3 x 60 s = 33.3%
        db.execute(sa.text("update student_note_time set seconds = 180 where student_id = :s and note_id = :n"), {"s": stu.id, "n": n1.id})
        db.execute(sa.text("""insert into student_mark_for_exercise (student_id, exercise_id, best_score_pct, exp_earned, attempts_count)
                              select :s, e.id, 80, 0, 1 from exercises e where false"""), {"s": stu.id})
        progress.complete_note(stu.id, n1.id)          # course started, 1 of 2 notes read
        g = progress.skill_graph(stu.id)
        t.check("reading = mean of (180/360, 60/180) = 41.7%", g["reading"] == 41.7, g["reading"])
        t.check("subjects = course completion (1 of 2 notes) = 50%", g["subjects"] == 50.0, g["subjects"])
        t.check("practice / exams still not started", g["practice"] is None and g["exams"] is None)
        db.execute(sa.text("update student_note_time set seconds = 99999 where student_id = :s"), {"s": stu.id})
        t.check("reading never above 100%", progress.skill_graph(stu.id)["reading"] == 100.0)

        t.section("daily tasks, streak, badges")
        led = lambda src: db.fetch_all(sa.text("select amount, xexp from exp_ledger where student_id = :s and source = :src"), {"s": stu.id, "src": src})  # noqa: E731
        t.check("finishing a note completes the task: +10 exp, +2 Xexp", [(r["amount"], float(r["xexp"])) for r in led("daily_task")] == [(10, 2.0)], led("daily_task"))
        t.check("first badge: First steps (+25 exp, +5 Xexp)", [(r["amount"], float(r["xexp"])) for r in led("badge")] == [(25, 5.0)])
        t.check("a study day counted -> 1-day streak", progress.skill_graph(stu.id)["counts"]["streak"] == 1)
        progress.complete_note(stu.id, n1.id)     # again: nothing more
        t.check("no double reward", len(led("daily_task")) == 1 and len(led("badge")) == 1)
        # the reading task: 10 minutes today (the time already counted above went in too)
        db.execute(sa.text("update student_daily_tasks set progress = 590 where student_id = :s and task_key = 'read_10'"), {"s": stu.id})
        db.execute(sa.text("update student_note_time set updated_at = now() - interval '1 minute' where student_id = :s"), {"s": stu.id})
        progress.add_reading_time(stu.id, n2.id, 30)
        t.check("reading task done by reading time", db.scalar(sa.text("select completed_at is not null from student_daily_tasks where student_id = :s and task_key = 'read_10'"), {"s": stu.id}))
        g = progress.skill_graph(stu.id)
        # First steps, and Ten hours: the 99,999 s set above (to test the 100% cap) is over 10 hours
        t.check("badges axis = 2 of 10 = 20% (First steps, Ten hours)", g["badges"] == 20.0, g["badges"])
        for i in range(1, 45):    # 44 earlier days in a row + today = 45
            db.execute(sa.text("insert into student_study_days (student_id, day) values (:s, :d) on conflict do nothing"),
                       {"s": stu.id, "d": day - __import__("datetime").timedelta(days=i)})
        db.execute(sa.text("update student_note_time set seconds = 360000 where student_id = :s and note_id = :n"), {"s": stu.id, "n": n1.id})
        g = progress.skill_graph(stu.id)
        t.check("45-day streak -> Streak axis full", (g["counts"]["streak"], g["streak"]) == (45, 100.0), g["counts"])
        t.check("100 hours -> Time axis full", g["time"] == 100.0, g["time"])
        with app.app_context():
            with db.session() as session:
                Engagement.check_badges(session, stu.id)
        got = {r[0] for r in db.fetch_all(sa.text("select badge_key from student_badges where student_id = :s"), {"s": stu.id}) for r in [tuple(r.values())]}
        t.check("streak and hours badges earned", {"streak_7", "streak_30", "streak_45", "hours_10"} <= got, got)

        t.section("notifications as pop-ups")
        home = c.get("/").get_data(as_text=True)
        t.check("pop-ups shown (badge, task)", 'class="toast toast-badge"' in home and "New badge: First steps" in home and "Daily task done" in home)
        t.check("shown once", "New badge: First steps" not in c.get("/courses").get_data(as_text=True))
        t.check("default: 10 seconds", 'data-seconds="10"' in home)
        r = c.post("/settings", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', c.get("/settings").get_data(as_text=True)).group(1),
                                      "section": "notifications", "toast_seconds": "25"})
        page = c.get("/courses").get_data(as_text=True)
        t.check("pop-ups off -> messages in the page", 'id="toasts"' not in page and 'class="flash"' in page)
        r = c.post("/settings", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', c.get("/settings").get_data(as_text=True)).group(1),
                                      "section": "notifications", "toast_enabled": "on", "toast_seconds": "25"})
        t.check("chosen duration used", 'data-seconds="25"' in c.get("/courses").get_data(as_text=True))

        t.section("pages")
        home = c.get("/").get_data(as_text=True)
        t.check("home: the student's own graph", "Your skill graph: Reading 100%" in home and "Streak 100%" in home and "Example" not in home)
        dash = c.get("/dashboard").get_data(as_text=True)
        t.check("lobby: player card, graph, tasks, achievements", all(x in dash for x in ("player-card", "SKILL GRAPH", "TODAY'S TASKS", "ACHIEVEMENTS", ">45<small> days")))
        t.check("home = the same lobby when logged in", "player-card" in home and "LEARN. PRACTISE. LEVEL UP." not in home)
        import io
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        tok2 = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/settings").get_data(as_text=True)).group(1)
        r = c.post("/settings", data={"csrf_token": tok2, "section": "photo", "photo": (io.BytesIO(png), "me.png")}, content_type="multipart/form-data")
        lobby = c.get("/").get_data(as_text=True)
        t.check("profile photo shown in the lobby and the header", r.status_code == 302 and 'class="avatar-img" src="/media/av_' in lobby and 'class="nav-photo"' in lobby)
        r = c.post("/settings", data={"csrf_token": tok2, "section": "photo", "photo": (io.BytesIO(b"<svg/>"), "x.png")}, content_type="multipart/form-data")
        t.check("not a picture -> refused", r.status_code == 400 and "PNG, JPEG, GIF or WebP" in r.get_data(as_text=True))
        c.post("/settings", data={"csrf_token": tok2, "section": "photo_remove"})
        t.check("photo removed -> initials again", 'class="avatar-img"' not in c.get("/").get_data(as_text=True))
        fresh = users.create(UserCreate(name="Brand New", email=f"sknew-{run}@{DOMAIN}", password="longenough1"))
        users.mark_email_verified(fresh.id, fresh.email)
        c2 = app.test_client()
        with c2.session_transaction() as s2:
            s2[SESSION_USER_KEY] = fresh.id
        new_home = c2.get("/").get_data(as_text=True)
        t.check("a new player gets their own lobby (zeros, tasks, start here), never the example", "player-card" in new_home
                and "Example" not in new_home and "TODAY'S TASKS" in new_home and "Reading not started" in new_home)
        t.check("graph has the seven axes", all(a in home for a in ("READING", "PRACTICE", "EXAMS", "SUBJECTS", "STREAK", "TIME", "BADGES")))
        guest = app.test_client().get("/").get_data(as_text=True)
        t.check("guests: the example", "Example skill graph" in guest and "Example of what you will see" in guest)
        t.check("table view for screen readers / print", "SHOW AS TABLE" in home)
    finally:
        db.execute(sa.text("delete from courses where created_by in (select id from users where email like :d)"), {"d": f"%@{DOMAIN}"})
        db.execute(sa.text("delete from users where email like :d"), {"d": f"%@{DOMAIN}"})
        print(f"\ncleanup: {db.scalar(sa.text('select count(*) from users where email like :d'), {'d': f'%@{DOMAIN}'})} test users left")
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
