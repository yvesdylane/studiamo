"""Imports every ORM model so SQLAlchemy's registry knows all of them.

Relationships refer to each other by class name, so import this module once
at startup and in alembic's env.py. Migrations use Base.metadata.
"""

from src.ai.UserAiKey import UserAiKey
from src.attempt.ExamAttempt import ExamAttempt
from src.attempt.ExamAttemptQuestion import ExamAttemptQuestion
from src.attempt.ExerciseAttempt import ExerciseAttempt
from src.base.Base import Base
from src.course.modles.Course import Course
from src.course.modles.Note import Note
from src.course.modles.Topic import Topic
from src.exam.models.Exam import Exam
from src.exam.models.ExamGuest import ExamAllowedEmail, ExamGuest
from src.exam.models.ExamQuestion import ExamQuestion
from src.exam.models.ExamTopic import ExamTopic
from src.exercise.models.Exercise import Exercise
from src.exercise.models.ExerciseQuestion import ExerciseQuestion
from src.exercise.models.NoteExercise import NoteExercise
from src.player.PlayerStats import PlayerStats
from src.progress.models.ExpLedger import ExpLedger
from src.progress.models.StudentCompletedNote import StudentCompletedNote
from src.progress.models.StudentNoteTime import StudentNoteTime
from src.progress.models.StudentMarkForExam import StudentMarkForExam
from src.progress.models.StudentMarkForExercise import StudentMarkForExercise
from src.question.models.Question import Question
from src.question.models.QuestionOption import QuestionOption
from src.question.models.QuestionTestCase import QuestionTestCase
from src.user.models.ApiToken import ApiToken
from src.user.models.LecturerApplication import LecturerApplication
from src.user.models.User import User
from src.user.models.UserIdentity import UserIdentity

__all__ = [
    "UserAiKey",
    "Base",
    "ExamAttempt",
    "ExamAttemptQuestion",
    "ExerciseAttempt",
    "Course",
    "Note",
    "Topic",
    "Exam",
    "ExamAllowedEmail",
    "ExamGuest",
    "ExamQuestion",
    "ExamTopic",
    "Exercise",
    "ExerciseQuestion",
    "NoteExercise",
    "PlayerStats",
    "ExpLedger",
    "StudentCompletedNote",
    "StudentNoteTime",
    "StudentMarkForExam",
    "StudentMarkForExercise",
    "Question",
    "QuestionOption",
    "QuestionTestCase",
    "ApiToken",
    "LecturerApplication",
    "User",
    "UserIdentity",
]
