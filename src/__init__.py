"""Learning platform - one package per module.

    src/<module>/<Name>.py      SQLAlchemy model(s) for one table
    src/<module>/<Name>Dto.py   Pydantic DTOs for that object
                                (Base / Create / Update / Read / Public)

Source of truth for the database is schema.sql (enum types, triggers,
functions and views live there); the models mirror it.

Import `src.models` once at startup (and in alembic's env.py): relationships
refer to each other by class name, so every model has to be imported before
the first query. Use `src.base.Base.Base.metadata` for migrations.

Server-managed values (student_id, attempt_no, scores, exp, rank, ledger
rows, best marks) never appear in Create DTOs, so a client cannot set them.
"""
