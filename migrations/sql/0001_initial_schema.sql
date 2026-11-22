-- =====================================================================
-- Learning platform schema v1 (PostgreSQL 12+)
-- Notes, exercises, exams, XP / level / rank
-- =====================================================================

-- ---------- ENUMS ----------
create type user_role       as enum ('student', 'lecturer');
create type player_rank     as enum ('F', 'E', 'D', 'C', 'B', 'A', 'S');
create type question_type   as enum ('mcq', 'short_answer', 'code');
create type code_language   as enum ('vbnet', 'c', 'javascript', 'typescript', 'python');
create type exam_kind       as enum ('system', 'scheduled');
create type attempt_status  as enum ('in_progress', 'submitted', 'graded');

-- =====================================================================
-- ACCOUNTS
-- =====================================================================
create table users (
    id            serial primary key,
    name          varchar(100) not null,
    player_name   varchar(50) unique,
    email         varchar(255) not null unique,
    password_hash text,                                  -- null if only external auth
    roles         user_role[] not null default '{student}',  -- a lecturer can also be a student
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create function set_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end
$$;

create trigger users_set_updated_at
before update on users
for each row execute function set_updated_at();

create table user_identities (                           -- google, github, ...
    id           serial primary key,
    user_id      integer not null references users(id) on delete cascade,
    provider     varchar(30) not null,
    provider_uid text not null,
    unique (provider, provider_uid)
);
create index user_identities_user_idx on user_identities (user_id);

-- ---------- player progression ----------
create table player_stats (
    user_id           integer primary key references users(id) on delete cascade,
    total_exp         bigint not null default 0 check (total_exp >= 0),  -- lifetime exp
    rank              player_rank not null default 'F',
    performance_score numeric(5,2) not null default 0,   -- 0-100, drives rank
    rank_updated_at   timestamptz
);
create index player_stats_total_exp_idx on player_stats (total_exp desc);  -- leaderboard

-- every user gets a stats row
create function create_player_stats() returns trigger
language plpgsql as $$
begin
    insert into player_stats (user_id) values (new.id);
    return new;
end
$$;

create trigger users_create_player_stats
after insert on users
for each row execute function create_player_stats();

-- Level L -> L+1 costs 10 * 2^L exp, starting at level 0.
-- Total exp needed to reach level L = 10 * (2^L - 1).
create function level_from_exp(total bigint) returns integer
language plpgsql immutable as $$
declare
    lvl integer := 0;
begin
    while lvl < 50 and 10 * ((1::bigint << (lvl + 1)) - 1) <= total loop
        lvl := lvl + 1;
    end loop;
    return lvl;
end
$$;

create view player_levels as
select p.user_id,
       p.total_exp,
       p.rank,
       l.level,
       p.total_exp - 10 * ((1::bigint << l.level) - 1) as exp_in_level,
       10 * (1::bigint << l.level)                     as exp_to_next_level
from player_stats p
cross join lateral (select level_from_exp(p.total_exp) as level) l;

-- =====================================================================
-- CONTENT
-- =====================================================================
create table courses (
    id          serial primary key,
    code        varchar(20) not null unique,
    title       varchar(200) not null,
    description text,
    created_by  integer not null references users(id),
    created_at  timestamptz not null default now()
);

create table course_topics (
    id        serial primary key,
    course_id integer not null references courses(id) on delete cascade,
    title     varchar(200) not null,
    position  integer not null,
    unique (course_id, position)
);

create table topic_notes (
    id         serial primary key,
    topic_id   integer not null references course_topics(id) on delete cascade,
    title      varchar(200) not null,
    md_path    text not null,                            -- path/key of the .md file
    position   integer not null,
    max_exp    integer not null default 0,
    published  boolean not null default false,
    created_at timestamptz not null default now()
);
create index topic_notes_topic_idx on topic_notes (topic_id, position);

-- =====================================================================
-- QUESTION BANK
-- =====================================================================
create table questions (
    id             serial primary key,
    topic_id       integer references course_topics(id) on delete set null,
    author_id      integer not null references users(id),
    type           question_type not null,
    body_md        text not null,
    difficulty     smallint check (difficulty between 1 and 5),
    points         numeric(5,2) not null default 1,
    explanation_md text,
    is_active      boolean not null default true,
    allow_multiple boolean not null default false,       -- mcq with several correct options
    code_language  code_language,                        -- code questions only
    starter_code   text,
    created_at     timestamptz not null default now(),
    check (type <> 'code' or code_language is not null)
);
create index questions_topic_idx on questions (topic_id) where is_active;

create table question_options (                          -- mcq
    id          serial primary key,
    question_id integer not null references questions(id) on delete cascade,
    label       text not null,
    is_correct  boolean not null default false
);
create index question_options_question_idx on question_options (question_id);

create table question_test_cases (                       -- code (stdin/stdout, works for every language)
    id              serial primary key,
    question_id     integer not null references questions(id) on delete cascade,
    input           text not null default '',
    expected_output text not null,
    is_hidden       boolean not null default true,
    weight          numeric(5,2) not null default 1
);
create index question_test_cases_question_idx on question_test_cases (question_id);

-- =====================================================================
-- EXERCISES
-- =====================================================================
create table exercises (
    id           serial primary key,
    topic_id     integer not null references course_topics(id) on delete cascade,
    title        varchar(200) not null,
    max_exp      integer not null default 0,
    retake_decay numeric(3,2) not null default 0.50      -- exp multiplier per extra attempt
                 check (retake_decay between 0 and 1),
    published    boolean not null default false
);
create index exercises_topic_idx on exercises (topic_id);

create table exercise_questions (
    exercise_id integer references exercises(id) on delete cascade,
    question_id integer references questions(id),
    position    integer not null,
    primary key (exercise_id, question_id)
);

create table note_exercises (                            -- exercise attached to a note
    note_id     integer references topic_notes(id) on delete cascade,
    exercise_id integer references exercises(id) on delete cascade,
    primary key (note_id, exercise_id)
);

-- =====================================================================
-- EXAMS
--   scheduled : lecturer-made, explicit pool (exam_questions), one attempt
--               inside [opens_at, closes_at]
--   system    : generated from the question bank (exam_topics), open any time,
--               unlimited attempts
-- =====================================================================
create table exams (
    id                    serial primary key,
    course_id             integer not null references courses(id) on delete cascade,
    created_by            integer references users(id),  -- null for system exams
    title                 varchar(200) not null,
    kind                  exam_kind not null,
    opens_at              timestamptz,
    closes_at             timestamptz,
    duration_minutes      integer,
    questions_per_attempt integer,                       -- null = whole pool
    shuffle_questions     boolean not null default true,
    shuffle_options       boolean not null default true,
    max_attempts          integer,                       -- null = unlimited
    max_exp               integer not null default 0,
    published             boolean not null default false,
    check (kind = 'system' or (opens_at is not null and closes_at is not null)),
    check (kind = 'system' or (created_by is not null and coalesce(max_attempts, 0) = 1)),
    check (opens_at is null or closes_at is null or closes_at > opens_at)
);
create index exams_course_idx on exams (course_id);
create index exams_kind_window_idx on exams (kind, opens_at, closes_at);

create table exam_questions (                            -- explicit pool (scheduled exams)
    exam_id     integer references exams(id) on delete cascade,
    question_id integer references questions(id),
    points      numeric(5,2),                            -- optional override
    primary key (exam_id, question_id)
);

create table exam_topics (                               -- pool source for system exams
    exam_id  integer references exams(id) on delete cascade,
    topic_id integer references course_topics(id) on delete cascade,
    primary key (exam_id, topic_id)
);

-- =====================================================================
-- ATTEMPTS
-- =====================================================================
create table exercise_attempts (
    id               serial primary key,
    student_id       integer not null references users(id) on delete cascade,
    exercise_id      integer not null references exercises(id) on delete cascade,
    attempt_no       integer not null,                   -- 1, 2, 3 ...
    status           attempt_status not null default 'in_progress',
    md_path          text,                               -- md file: questions asked + student's answers
    points_earned    numeric(7,2),
    points_possible  numeric(7,2),
    score_pct        numeric(5,2) check (score_pct between 0 and 100),
    started_at       timestamptz not null default now(),
    submitted_at     timestamptz,
    duration_seconds integer generated always as (       -- time taken, computed by the DB
                         extract(epoch from (submitted_at - started_at))::integer
                     ) stored,
    unique (student_id, exercise_id, attempt_no),
    check (status = 'in_progress' or (md_path is not null and submitted_at is not null)),
    check (status <> 'graded' or score_pct is not null),
    check (points_earned is null or (points_earned >= 0 and points_earned <= points_possible))
);

create table exam_attempts (
    id               serial primary key,
    student_id       integer not null references users(id) on delete cascade,
    exam_id          integer not null references exams(id) on delete cascade,
    attempt_no       integer not null,
    status           attempt_status not null default 'in_progress',
    md_path          text,                               -- md file: questions asked + student's answers
    points_earned    numeric(7,2),
    points_possible  numeric(7,2),
    score_pct        numeric(5,2) check (score_pct between 0 and 100),
    started_at       timestamptz not null default now(),
    submitted_at     timestamptz,
    duration_seconds integer generated always as (
                         extract(epoch from (submitted_at - started_at))::integer
                     ) stored,
    unique (student_id, exam_id, attempt_no),
    check (status = 'in_progress' or (md_path is not null and submitted_at is not null)),
    check (status <> 'graded' or score_pct is not null),
    check (points_earned is null or (points_earned >= 0 and points_earned <= points_possible))
);

create table exam_attempt_questions (                    -- the subset THIS attempt got
    attempt_id  integer references exam_attempts(id) on delete cascade,
    question_id integer references questions(id),
    position    integer not null,
    primary key (attempt_id, question_id)
);
create index exam_attempt_questions_question_idx on exam_attempt_questions (question_id);

-- Individual answers are NOT stored as rows. When an attempt is submitted the
-- app writes one md file (the "attempt record") holding a snapshot of each
-- question, the student's answer, the correct answer / test results and the
-- points, then stores its location in exercise_attempts.md_path /
-- exam_attempts.md_path. The snapshot matters: questions in the bank may be
-- edited later, but the record must show what the student actually saw.
-- Scores and time taken stay in the DB so rank, exp and history need no md parsing.

-- =====================================================================
-- STUDENT PROGRESS & EXP
--
-- Notes     : full max_exp, once, when completed.
-- Exercises : attempt n awards
--               floor(max_exp * score_pct/100 * retake_decay^(n-1))
-- Exams     : exp for the task = floor(max_exp * best_score_pct/100);
--             award only the difference from exp_earned (if positive).
-- Every award goes to exp_ledger and is added to player_stats.total_exp.
-- =====================================================================
create table student_completed_notes (
    student_id   integer references users(id) on delete cascade,
    note_id      integer references topic_notes(id) on delete cascade,
    exp_awarded  integer not null default 0,
    completed_at timestamptz not null default now(),
    primary key (student_id, note_id)
);
create index student_completed_notes_note_idx on student_completed_notes (note_id);

create table student_mark_for_exercise (
    student_id     integer references users(id) on delete cascade,
    exercise_id    integer references exercises(id) on delete cascade,
    best_score_pct numeric(5,2) not null,
    exp_earned     integer not null default 0,           -- cumulative
    attempts_count integer not null default 1,
    primary key (student_id, exercise_id)
);

create table student_mark_for_exams (
    student_id     integer references users(id) on delete cascade,
    exam_id        integer references exams(id) on delete cascade,
    best_score_pct numeric(5,2) not null,
    exp_earned     integer not null default 0,
    attempts_count integer not null default 1,
    primary key (student_id, exam_id)
);

create table exp_ledger (
    id         bigserial primary key,
    student_id integer not null references users(id) on delete cascade,
    source     varchar(20) not null check (source in ('note', 'exercise', 'exam')),
    source_id  integer not null,
    amount     integer not null,
    created_at timestamptz not null default now()
);
create index exp_ledger_student_idx on exp_ledger (student_id, created_at desc);

-- A student is "in" a course as soon as they touch anything in it
create view student_courses as
select scn.student_id, ct.course_id
from student_completed_notes scn
join topic_notes tn   on tn.id = scn.note_id
join course_topics ct on ct.id = tn.topic_id
union
select ea.student_id, ct.course_id
from exercise_attempts ea
join exercises e      on e.id = ea.exercise_id
join course_topics ct on ct.id = e.topic_id
union
select xa.student_id, x.course_id
from exam_attempts xa
join exams x on x.id = xa.exam_id; 