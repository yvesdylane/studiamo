"""Several AI providers for importing notes: Claude (default), Gemini, OpenAI.

    uv run python -m tests.test_ai_providers

The providers' servers are faked (no real key, nothing billed). The saved keys
and settings go to the real database in .env; the test account uses
@studiamo-test.example and is deleted at the end. Do not run it at the same
time as another web test: they share that cleanup.
"""

from __future__ import annotations

import io
import re
import sys
import uuid

import sqlalchemy as sa

from tests.harness import Checks
from tests.test_web_auth import DOMAIN, FakeMailer, token


class FakeResponse:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.text = str(body)

    def json(self) -> dict:
        return self._body


class FakeHttp:
    """Stands in for requests.request: answers by URL, remembers what was sent."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.answers: list[tuple[str, int, dict]] = []   # (url part, status, body), first match wins, used once

    def add(self, url_part: str, status: int, body: dict) -> None:
        self.answers.append((url_part, status, body))

    def __call__(self, method, url, timeout=None, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for i, (part, status, body) in enumerate(self.answers):
            if part in url:
                self.answers.pop(i)
                return FakeResponse(status, body)
        raise AssertionError(f"unexpected call {method} {url}")


GEMINI_MODELS = {"models": [
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
    {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemma-3-27b-it", "supportedGenerationMethods": ["generateContent"]},
]}
OPENAI_MODELS = {"data": [
    {"id": "gpt-5-mini", "created": 300}, {"id": "gpt-5", "created": 200}, {"id": "o3", "created": 100},
    {"id": "text-embedding-3-large", "created": 400}, {"id": "gpt-4o-realtime-preview", "created": 500},
    {"id": "dall-e-3", "created": 50}, {"id": "gpt-5-2025-08-07", "created": 250},
]}
NOTE = "# Week 1\n\n## Loops\n\nA loop repeats.\n"


def gemini_answer(text: str, reason: str = "STOP") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": reason}], "modelVersion": "gemini-3-pro"}


def openai_answer(text: str | None, reason: str = "stop", refusal: str | None = None) -> dict:
    return {"model": "gpt-5", "choices": [{"finish_reason": reason, "message": {"content": text, "refusal": refusal}}]}


def main() -> int:
    from src.ai import providers as P
    from src.ai.importers import Imported
    from src.app import create_app
    from src.user.dto.UserDto import UserCreate, UserRolesUpdate
    from src.user.models.User import UserRole
    from src.user.service.UserService import UserService

    t = Checks()
    http = FakeHttp()
    real_request = P.requests.request
    P.requests.request = http
    gem, oai, claude = P.PROVIDERS["google"], P.PROVIDERS["openai"], P.PROVIDERS["anthropic"]
    real_claude_check = claude.check

    app = create_app()
    app.extensions["mailer"] = FakeMailer()
    db = app.extensions["db"]
    run = uuid.uuid4().hex[:6]
    try:
        t.section("Gemini")
        http.add("/models", 200, GEMINI_MODELS)
        models = gem.check("AIzaFAKEKEY")
        t.check("only Gemini text models, newest and pro first", models == ["gemini-3-pro", "gemini-2.5-flash"], models)
        t.check("key sent in a header, not the URL", http.calls[-1]["headers"]["x-goog-api-key"] == "AIzaFAKEKEY" and "AIza" not in http.calls[-1]["url"])
        t.expect_error("key format checked before any call", P.AiKeyError, lambda: gem.check_format("sk-ant-xyz"))
        http.add("/models", 400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        t.expect_error("bad key -> 'refused this API key'", P.AiKeyError, lambda: gem.check("AIzaWRONG"))
        http.add(":generateContent", 200, gemini_answer(NOTE))
        r = gem.ask("AIzaK", "gemini-3-pro", "SYSTEM", [P.Turn("user", "convert", pdf=b"%PDF-1.4 x")])
        body = http.calls[-1]["json"]
        t.check("answer text and model come back", r.text == NOTE and r.model == "gemini-3-pro")
        t.check("system instructions and the PDF are sent", body["systemInstruction"]["parts"][0]["text"] == "SYSTEM"
                and body["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "application/pdf")
        http.add(":generateContent", 200, gemini_answer("half", "MAX_TOKENS"))
        t.expect_error("cut off -> 'too long'", P.AiKeyError, lambda: gem.ask("AIzaK", "m", "S", [P.Turn("user", "x")]))
        http.add(":generateContent", 200, {"promptFeedback": {"blockReason": "SAFETY"}})
        t.expect_error("blocked -> 'declined'", P.AiKeyError, lambda: gem.ask("AIzaK", "m", "S", [P.Turn("user", "x")]))
        http.add(":generateContent", 429, {"error": {"message": "You exceeded your current quota"}})
        try:
            gem.ask("AIzaK", "m", "S", [P.Turn("user", "x")])
        except P.AiKeyError as exc:
            t.check("quota -> says no quota left", "quota" in str(exc))

        t.section("OpenAI")
        http.add("/models", 200, OPENAI_MODELS)
        models = oai.check("sk-proj-FAKE")
        t.check("only chat models, newest first", models == ["gpt-5-mini", "gpt-5-2025-08-07", "gpt-5", "o3"], models)
        t.check("default: newest full GPT (not mini, not a dated snapshot)", oai.default_model(models) == "gpt-5")
        t.expect_error("an Anthropic key is refused under OpenAI", P.AiKeyError, lambda: oai.check_format("sk-ant-abc"))
        http.add("/models", 401, {"error": {"message": "Incorrect API key provided"}})
        t.expect_error("bad key -> refused", P.AiKeyError, lambda: oai.check("sk-WRONG"))
        http.add("/chat/completions", 200, openai_answer(NOTE))
        turns = [P.Turn("user", "convert", pdf=b"%PDF-1.4 x"), P.Turn("assistant", "draft"), P.Turn("user", "fix it")]
        r = oai.ask("sk-K", "gpt-5", "SYSTEM", turns)
        msgs = http.calls[-1]["json"]["messages"]
        t.check("system first, PDF as a file part, the whole conversation sent",
                msgs[0] == {"role": "system", "content": "SYSTEM"} and msgs[1]["content"][0]["type"] == "file"
                and msgs[1]["content"][0]["file"]["file_data"].startswith("data:application/pdf;base64,")
                and msgs[2] == {"role": "assistant", "content": "draft"} and len(msgs) == 4)
        t.check("bearer key in the header", http.calls[-1]["headers"]["Authorization"] == "Bearer sk-K")
        http.add("/chat/completions", 200, openai_answer(None, refusal="I can't help with that."))
        t.expect_error("refusal -> 'declined'", P.AiKeyError, lambda: oai.ask("sk-K", "gpt-5", "S", [P.Turn("user", "x")]))
        http.add("/chat/completions", 200, openai_answer("half", "length"))
        t.expect_error("cut off -> 'too long'", P.AiKeyError, lambda: oai.ask("sk-K", "gpt-5", "S", [P.Turn("user", "x")]))
        http.add("/chat/completions", 404, {"error": {"message": "The model does not exist"}})
        t.expect_error("unknown model -> choose another", P.AiKeyError, lambda: oai.ask("sk-K", "gpt-x", "S", [P.Turn("user", "x")]))

        t.section("saving keys, the default, and converting")
        users = UserService(db)
        u = users.create(UserCreate(name="Ada Ai", email=f"adaai-{run}@{DOMAIN}", password="correct-horse-9", accepted_terms=True))
        users.mark_email_verified(u.id, u.email)
        users.set_roles(u.id, UserRolesUpdate(roles=[UserRole.STUDENT, UserRole.LECTURER]))
        with app.app_context():
            from src.web import deps

            conv = deps.converter()
        s = conv.settings(u.id)
        t.check("nothing connected; Claude is the default", s == {"default": "anthropic", "connected": {}})
        t.expect_error("convert with no key asks for one", P.AiKeyError, lambda: conv.convert(u.id, Imported(kind="md", text="x", markdown="x")))
        http.add("/models", 200, GEMINI_MODELS)
        conv.save_key(u.id, "google", "AIzaFIRSTKEY")
        s = conv.settings(u.id)
        t.check("Gemini saved with its models and the best one picked",
                s["connected"]["google"]["model"] == "gemini-3-pro" and s["connected"]["google"]["hint"] == "…TKEY")
        t.check("the first key saved becomes the default", s["default"] == "google")
        stored = db.fetch_all("select key_encrypted from user_ai_keys where user_id = :u", {"u": u.id})[0]["key_encrypted"]
        t.check("stored encrypted, not in clear", "AIzaFIRSTKEY" not in stored)
        claude.check = lambda key: [m for m, _ in P.CLAUDE_MODELS]
        conv.save_key(u.id, "anthropic", "sk-ant-FAKE1234")
        t.check("a second key does not change the default", conv.settings(u.id)["default"] == "google")
        conv.set_model(u.id, "google", "gemini-2.5-flash")
        conv.set_model(u.id, "google", "not-a-model")
        t.check("model choice saved; unknown model ignored", conv.settings(u.id)["connected"]["google"]["model"] == "gemini-2.5-flash")

        http.add(":generateContent", 200, gemini_answer(NOTE))
        out = conv.convert(u.id, Imported(kind="md", text="loops", markdown="loops"), title="Week 1")
        t.check("convert uses the default (Gemini) and its model", out.provider == "google" and out.markdown == NOTE
                and "gemini-2.5-flash:generateContent" in http.calls[-1]["url"])
        t.check("the user's notes and the title are in the request", "loops" in str(http.calls[-1]["json"]) and "Week 1" in str(http.calls[-1]["json"]))
        claude.ask = lambda key, model, system, turns: P.Reply(text="```markdown\n" + NOTE + "```", model=model)
        out = conv.convert(u.id, Imported(kind="md", text="x", markdown="x"), provider="anthropic")
        t.check("picking Claude for one import uses Claude (and strips a ``` fence)", out.provider == "anthropic" and out.markdown == NOTE)
        t.check("asking for an AI with no key falls back to the default", conv.pick(u.id, "openai") == "google")
        conv.set_default(u.id, "anthropic")
        t.check("default switched to Claude", conv.pick(u.id) == "anthropic")
        http.add(":generateContent", 200, gemini_answer("```diagram\nbroken @@@\n```"))
        http.add(":generateContent", 200, gemini_answer(NOTE))
        out = conv.convert(u.id, Imported(kind="md", text="x", markdown="x"), provider="google")
        sent = http.calls[-1]["json"]["contents"]
        t.check("renderer problems -> one fix round with the draft and the problems",
                len(sent) == 3 and sent[1]["role"] == "model" and "problems" in sent[2]["parts"][0]["text"] and out.markdown == NOTE)

        t.section("pages")
        c = app.test_client()
        c.post("/login", data={"csrf_token": token(c, "/login"), "email": u.email, "password": "correct-horse-9"})
        page = c.get("/settings?tab=ai").get_data(as_text=True)
        t.check("Settings shows Claude, Gemini and OpenAI", all(f'id="ai-{p}"' in page for p in ("anthropic", "google", "openai")))
        t.check("...with the default choice (Claude checked, OpenAI disabled)",
                re.search(r'value="anthropic" checked', page) and re.search(r'value="openai"\s*disabled', page))
        t.check("...and Gemini CLI setup for MCP", "~/.gemini/settings.json" in page and "ChatGPT" in page)
        tok = token(c, "/settings")
        r = c.post("/settings", data={"csrf_token": tok, "section": "ai_key_save", "provider": "openai", "api_key": "sk-ant-oops"})
        body = r.get_data(as_text=True)
        t.check("wrong key under OpenAI -> error shown in the OpenAI block", r.status_code == 400 and 'id="ai-err-openai"' in body and "save it under Claude" in body)
        r = c.post("/settings", data={"csrf_token": tok, "section": "ai_default", "provider": "google"})
        t.check("default changed from the page", r.status_code == 302 and conv.settings(u.id)["default"] == "google")
        r = c.post("/settings", data={"csrf_token": tok, "section": "ai_default", "provider": "nope"})
        t.check("unknown provider -> 400", r.status_code == 400)

        course = c.post("/studio/courses", data={"csrf_token": token(c, "/studio/"), "code": f"AI{run}", "title": f"AI course {run}"})
        course_id = int(re.search(r"/studio/courses/(\d+)", course.headers["Location"]).group(1))
        c.post(f"/studio/courses/{course_id}/topics", data={"csrf_token": tok, "title": "Week 1"})
        topic_id = int(re.findall(r'id="topic-(\d+)"', c.get(f"/studio/courses/{course_id}").get_data(as_text=True))[0])
        r = c.post(f"/studio/topics/{topic_id}/notes", data={"csrf_token": tok, "title": "Loops"})
        note_id = int(re.search(r"/studio/notes/(\d+)", r.headers["Location"]).group(1))
        editor = c.get(f"/studio/notes/{note_id}").get_data(as_text=True)
        t.check("Import dialog lets you pick the AI, default selected",
                'id="import-provider"' in editor and re.search(r'value="google" selected>Gemini', editor) and ">Claude" in editor)
        http.add(":generateContent", 200, gemini_answer(NOTE))
        r = c.post(f"/studio/notes/{note_id}/import", data={"csrf_token": tok, "text": "loops", "use_ai": "1"})
        t.check("import uses the default AI", r.status_code == 200 and r.get_json()["provider"] == "google")
        r = c.post(f"/studio/notes/{note_id}/import", data={"csrf_token": tok, "use_ai": "1", "provider": "anthropic",
                                                            "file": (io.BytesIO(b"# Old\n\ntext"), "old.md")}, content_type="multipart/form-data")
        t.check("import with Claude picked uses Claude", r.status_code == 200 and r.get_json()["provider"] == "anthropic")
        c.post("/settings", data={"csrf_token": tok, "section": "ai_key_delete", "provider": "google"})
        s = conv.settings(u.id)
        t.check("removing Gemini keeps Claude", list(s["connected"]) == ["anthropic"] and conv.pick(u.id) == "anthropic")
    finally:
        P.requests.request = real_request
        claude.check = real_claude_check
        claude.__dict__.pop("ask", None)
        d = {"d": f"%@{DOMAIN}"}
        db.execute(sa.text("delete from courses where created_by in (select id from users where email like :d)"), d)
        db.execute(sa.text("delete from users where email like :d"), d)
    return t.summary()


if __name__ == "__main__":
    sys.exit(main())
