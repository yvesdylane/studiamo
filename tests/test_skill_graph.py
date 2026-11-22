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

        t.section("estimated reading time")
        t.check("saved with the note: 400 words -> 120 s", saved.reading_seconds == 120, saved.reading_seconds)

        t.section("nothing done yet")
        g = progress.skill_graph(stu.id)
        t.check("every axis 'not started'", [g[k] for k in ("reading", "practice", "exams", "subjects")] == [None] * 4, g)

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

        t.section("pages")
        home = c.get("/").get_data(as_text=True)
        t.check("home: the student's own graph", "Your skill graph: Reading 100%" in home and "Example of what you will see" not in home)
        t.check("dashboard: graph and explanation", "YOUR SKILL GRAPH" in c.get("/dashboard").get_data(as_text=True))
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
