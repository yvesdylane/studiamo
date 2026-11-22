"""Daily tasks, streaks, badges and notifications (migration 0016).

Change the tasks, badges and rewards here: nothing else depends on the numbers.

  daily tasks   3 a day per student, drawn from TASKS; each gives TASK_REWARD,
                all three together ALL_TASKS_BONUS
  streak        days in a row with at least one daily task done
  badges        BADGES; each gives BADGE_REWARD when earned
  study time    reading time + time on exercise and exam attempts

Every function that takes a session runs inside the caller's transaction (the
note completion, the graded attempt...), so the reward and the event are saved
together. Messages for the student go to user_notifications and show up as
pop-ups on their next page.

The day is the calendar day in APP_TIMEZONE (.env, default UTC).
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Task:
    key: str
    title: str
    target: int           # in the task's unit (seconds, notes, attempts)
    unit: str = ""


TASKS = [
    Task("read_10", "Read your notes for 10 minutes", 600, "s"),
    Task("note_1", "Finish a note (mark it as read)", 1),
    Task("exercise_1", "Finish an exercise", 1),
    Task("exercise_75", "Score 75% or more on an exercise", 1),
    Task("exam_1", "Take an exam (a practice one counts)", 1),
]
TASKS_PER_DAY = 3
TASK_REWARD = (10, Decimal("2"))          # exp, Xexp for each daily task
ALL_TASKS_BONUS = (20, Decimal("5"))      # all of today's tasks done

STREAK_FULL = 45                          # skill graph: Streak axis full at 45 days
TIME_FULL_SECONDS = 100 * 3600            # skill graph: Time axis full at 100 hours


@dataclass(frozen=True)
class Badge:
    key: str
    name: str
    description: str


BADGES = [
    Badge("first_steps", "First steps", "Finish your first note."),
    Badge("bookworm", "Bookworm", "Finish 10 notes."),
    Badge("perfect_practice", "Perfect practice", "Score 100% on an exercise."),
    Badge("exam_ace", "Exam ace", "Score 80% or more in an exam."),
    Badge("streak_7", "One week", "Keep a 7-day streak."),
    Badge("streak_30", "One month", "Keep a 30-day streak."),
    Badge("streak_45", "Unstoppable", "Keep a 45-day streak."),
    Badge("hours_10", "Ten hours", "Study for 10 hours in total."),
    Badge("all_tasks", "Full day", "Do all your daily tasks in one day."),
    Badge("course_done", "Course complete", "Finish every note and exercise of a course."),
]
BADGE_REWARD = (25, Decimal("5"))         # exp, Xexp for each badge


def today() -> date:
    return datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "UTC"))).date()


def notify(session: Session, user_id: int, text: str, kind: str = "info") -> None:
    session.execute(sa.text("insert into user_notifications (user_id, text, kind) values (:u, :t, :k)"),
                    {"u": user_id, "t": text[:300], "k": kind})


# ------------------------------------------------------------- daily tasks
def ensure_tasks(session: Session, student_id: int, day: date | None = None) -> None:
    """Today's tasks, chosen the same way every time for this student and day."""
    day = day or today()
    exists = session.execute(sa.text("select 1 from student_daily_tasks where student_id = :s and day = :d limit 1"),
                             {"s": student_id, "d": day}).first()
    if exists:
        return
    chosen = random.Random(f"{student_id}:{day.isoformat()}").sample(TASKS, TASKS_PER_DAY)
    for task in chosen:
        session.execute(sa.text("""insert into student_daily_tasks (student_id, day, task_key, target)
                                   values (:s, :d, :k, :t) on conflict do nothing"""),
                        {"s": student_id, "d": day, "k": task.key, "t": task.target})


def _advance(session: Session, student_id: int, task_key: str, amount: int) -> None:
    from src.progress.service.ProgressService import award_exp

    day = today()
    row = session.execute(sa.text("""select id, target, progress, completed_at from student_daily_tasks
                                     where student_id = :s and day = :d and task_key = :k for update"""),
                          {"s": student_id, "d": day, "k": task_key}).first()
    if row is None or row.completed_at is not None:      # not one of today's tasks, or already done
        return
    progress = min(row.target, row.progress + amount)
    done = progress >= row.target
    session.execute(sa.text("""update student_daily_tasks set progress = :p,
                                      completed_at = case when :done then now() else null end where id = :i"""),
                    {"p": progress, "done": done, "i": row.id})
    if not done:
        return
    task = next(t for t in TASKS if t.key == task_key)
    award_exp(session, student_id, "daily_task", row.id, TASK_REWARD[0], TASK_REWARD[1])
    notify(session, student_id, f"Daily task done: {task.title}. +{TASK_REWARD[0]} exp, +{TASK_REWARD[1]} Xexp.", "success")
    first_today = session.execute(sa.text("""insert into student_study_days (student_id, day) values (:s, :d)
                                            on conflict do nothing returning day"""), {"s": student_id, "d": day}).first()
    if first_today:
        streak = current_streak(session, student_id)
        if streak > 1:
            notify(session, student_id, f"{streak}-day streak! Keep it going tomorrow.", "success")
    left = session.execute(sa.text("""select count(*) from student_daily_tasks
                                      where student_id = :s and day = :d and completed_at is null"""),
                           {"s": student_id, "d": day}).scalar()
    if left == 0:
        award_exp(session, student_id, "daily_task", 0, ALL_TASKS_BONUS[0], ALL_TASKS_BONUS[1])
        notify(session, student_id, f"All of today's tasks done! Bonus +{ALL_TASKS_BONUS[0]} exp, +{ALL_TASKS_BONUS[1]} Xexp.", "success")


# ------------------------------------------------------------------ events
def on_reading(session: Session, student_id: int, seconds: int) -> None:
    if seconds > 0:
        ensure_tasks(session, student_id)
        _advance(session, student_id, "read_10", seconds)
        check_badges(session, student_id)


def on_note_completed(session: Session, student_id: int) -> None:
    ensure_tasks(session, student_id)
    _advance(session, student_id, "note_1", 1)
    check_badges(session, student_id)


def on_exercise_graded(session: Session, student_id: int, score_pct: Decimal) -> None:
    ensure_tasks(session, student_id)
    _advance(session, student_id, "exercise_1", 1)
    if score_pct >= 75:
        _advance(session, student_id, "exercise_75", 1)
    check_badges(session, student_id)


def on_exam_graded(session: Session, student_id: int, score_pct: Decimal) -> None:
    ensure_tasks(session, student_id)
    _advance(session, student_id, "exam_1", 1)
    check_badges(session, student_id)


# ------------------------------------------------------------- measures
def current_streak(session: Session, student_id: int) -> int:
    """Days in a row with a daily task done, ending today (or yesterday: today is not over)."""
    days = {r[0] for r in session.execute(sa.text(
        "select day from student_study_days where student_id = :s and day >= :since"),
        {"s": student_id, "since": today() - timedelta(days=400)})}
    d = today() if today() in days else today() - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def study_seconds(session: Session, student_id: int) -> int:
    return int(session.execute(sa.text(
        """select coalesce((select sum(seconds) from student_note_time where student_id = :s), 0)
                + coalesce((select sum(duration_seconds) from exercise_attempts where student_id = :s), 0)
                + coalesce((select sum(duration_seconds) from exam_attempts where student_id = :s), 0)"""),
        {"s": student_id}).scalar() or 0)


def _earned(session: Session, student_id: int) -> set[str]:
    return set(_earned_dates(session, student_id))


def _earned_dates(session: Session, student_id: int) -> dict:
    return {r[0]: r[1] for r in session.execute(sa.text("select badge_key, earned_at from student_badges where student_id = :s"),
                                                 {"s": student_id})}


def check_badges(session: Session, student_id: int) -> None:
    """Award every badge whose condition is now met (each only once)."""
    from src.progress.service.ProgressService import award_exp, refresh_rank

    have = _earned(session, student_id)
    if len(have) == len(BADGES):
        return
    q = lambda sql: session.execute(sa.text(sql), {"s": student_id}).scalar()   # noqa: E731
    notes_done = q("select count(*) from student_completed_notes where student_id = :s")
    streak = current_streak(session, student_id)
    tests = {
        "first_steps": lambda: notes_done >= 1,
        "bookworm": lambda: notes_done >= 10,
        "perfect_practice": lambda: q("select count(*) from student_mark_for_exercise where student_id = :s and best_score_pct >= 100") > 0,
        "exam_ace": lambda: q("select count(*) from student_mark_for_exams where student_id = :s and best_score_pct >= 80") > 0,
        "streak_7": lambda: streak >= 7,
        "streak_30": lambda: streak >= 30,
        "streak_45": lambda: streak >= 45,
        "hours_10": lambda: study_seconds(session, student_id) >= 36000,
        "all_tasks": lambda: q("""select count(*) from (select day from student_daily_tasks where student_id = :s
                                  group by day having count(*) = count(completed_at)) d""") > 0,
        "course_done": lambda: q(
            """select count(*) from (select distinct ct.course_id from student_completed_notes c
                 join topic_notes n on n.id = c.note_id join course_topics ct on ct.id = n.topic_id
                where c.student_id = :s) started
               where not exists (select 1 from topic_notes n join course_topics t on t.id = n.topic_id
                                  where t.course_id = started.course_id and n.published
                                    and not exists (select 1 from student_completed_notes c where c.student_id = :s and c.note_id = n.id))
                 and not exists (select 1 from exercises e join course_topics t on t.id = e.topic_id
                                  where t.course_id = started.course_id and e.published
                                    and not exists (select 1 from exercise_attempts a where a.student_id = :s
                                                     and a.exercise_id = e.id and a.status = 'graded'))""") > 0,
    }
    new = False
    for i, badge in enumerate(BADGES):
        if badge.key in have or not tests[badge.key]():
            continue
        inserted = session.execute(sa.text("""insert into student_badges (student_id, badge_key) values (:s, :k)
                                              on conflict do nothing returning badge_key"""), {"s": student_id, "k": badge.key}).first()
        if inserted:
            new = True
            award_exp(session, student_id, "badge", i + 1, BADGE_REWARD[0], BADGE_REWARD[1])
            notify(session, student_id, f"New badge: {badge.name}! {badge.description} +{BADGE_REWARD[0]} exp, +{BADGE_REWARD[1]} Xexp.", "badge")
    if new:
        refresh_rank(session, student_id)


# ------------------------------------------------------------ for pages
def summary(session: Session, student_id: int) -> dict:
    """Today's tasks, the streak, study time and badges, for the dashboard and the skill graph."""
    ensure_tasks(session, student_id)
    rows = session.execute(sa.text("""select task_key, target, progress, completed_at from student_daily_tasks
                                      where student_id = :s and day = :d order by id"""),
                           {"s": student_id, "d": today()}).all()
    by_key = {t.key: t for t in TASKS}
    tasks = [{"title": by_key[r.task_key].title, "target": r.target, "progress": r.progress, "done": r.completed_at is not None,
              "unit": by_key[r.task_key].unit} for r in rows if r.task_key in by_key]
    have = _earned_dates(session, student_id)
    return {
        "tasks": tasks,
        "task_reward": TASK_REWARD, "bonus": ALL_TASKS_BONUS,
        "streak": current_streak(session, student_id),
        "seconds": study_seconds(session, student_id),
        "badges": [{"key": b.key, "name": b.name, "description": b.description, "earned": b.key in have,
                    "earned_at": have.get(b.key)} for b in BADGES],
        "badges_earned": len(set(have) & {b.key for b in BADGES}),
    }


def take_notifications(session: Session, user_id: int, limit: int = 8) -> list[dict]:
    """Unseen messages for this user, marked as seen."""
    rows = session.execute(sa.text(
        """update user_notifications set seen_at = now()
            where id in (select id from user_notifications where user_id = :u and seen_at is null order by id limit :n)
        returning id, text, kind"""), {"u": user_id, "n": limit}).all()
    return [{"text": r.text, "kind": r.kind} for r in sorted(rows, key=lambda r: r.id)]
