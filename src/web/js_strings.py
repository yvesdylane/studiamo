"""Text the browser scripts show (static/js/*.js and the inline scripts),
listed here so pybabel extracts it. base.html hands the translations to the
page as window.I18N, and the scripts look them up with tr("…").
"""

from __future__ import annotations

from flask_babel import gettext


def N_(text: str) -> str:
    """Marks text for extraction; translated later, in table()."""
    return text

JS_STRINGS = [
    # exam-guard.js
    N_("LEAVE THE EXAM?"),
    N_("Going back or leaving this page submits your exam now, with the answers you have given so far. You cannot come back to it."),
    N_("SUBMIT AND LEAVE"),
    N_("STAY IN THE EXAM"),
    N_("Your browser did not allow full screen. Press the button again, or allow full screen for this site."),
    N_("You left full screen. The questions are hidden until you go back to full screen."),
    N_("YOU LEFT THE EXAM"),
    N_("Leaving the exam (another tab, another app, or minimising) is recorded and your lecturer sees it."),
    N_("If you leave once more, your exam is submitted automatically."),
    N_("After %(n)s more times, your exam is submitted automatically."),
    N_("BACK TO THE EXAM"),
    N_("START THE EXAM »"),
    N_("This device cannot show the exam in full screen. Stay on this page: leaving it is still detected."),
    # note-editor.js
    N_("Edit"),
    N_("Move up"),
    N_("Move down"),
    N_("Delete block"),
    N_("Source of this block"),
    N_("APPLY"),
    N_("CANCEL"),
    N_("Link address (https://…)"),
    N_("Upload failed"),
    N_("Choose a file or paste some text."),
    N_("Converting with your AI… this can take a minute or two."),
    N_("Importing…"),
    N_("Import failed"),
    N_("Converted by %(model)s."),
    N_("Imported without AI."),
    N_("Check the %(n)s problem(s) shown above the preview."),
    N_("Ctrl+Z undoes it. Save when it looks right."),
    # smd.js
    N_("Stopped: the program ran for more than %(n)s s."),
    N_("Loading Python (the first run takes a few seconds)…"),
    N_("Python did not load. Check your connection and try again."),
    N_("Running…"),
    # tests/take.html
    N_("%(n)s question(s) have no answer. Submit anyway?"),
]


def table() -> dict[str, str]:
    """{english: translation} for the current language (empty in English)."""
    return {s: t for s in JS_STRINGS if (t := gettext(s)) != s}
