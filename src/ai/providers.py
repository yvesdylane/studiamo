"""The AI providers a user can connect with their own API key: Claude
(Anthropic, the default), Gemini (Google) and OpenAI (ChatGPT's models).

Each provider can:
    check(key)           -> the model ids this key can use (raises AiKeyError if refused)
    default_model(ids)   -> the model picked when the key is first saved
    label(model)         -> how a model is shown in a menu
    ask(key, model, system, turns) -> Reply

A conversation is a list of Turn: the user's first message (text, plus the PDF
for PDF imports), then, when the note had problems, the model's answer and the
request to fix them. Models of Gemini and OpenAI are not hard-coded: they are
asked from the provider with the user's key, so new models show up by themselves.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import anthropic
import requests

TIMEOUT = (15, 900)   # connect, read: long notes take minutes


class AiKeyError(Exception):
    """Something the user must fix (wrong key, no credit…), in plain words."""


@dataclass
class Turn:
    role: str                 # "user" | "assistant"
    text: str
    pdf: bytes | None = None  # only on the first user turn of a PDF import


@dataclass
class Reply:
    text: str
    model: str


class Provider:
    id = ""
    name = ""            # "Claude"
    company = ""         # "Anthropic"
    key_prefix = ""      # what keys start with (checked before any call)
    placeholder = ""
    console_url = ""
    console_name = ""
    note = ""            # one line about cost / free tier

    def check_format(self, key: str) -> None:
        if not key.startswith(self.key_prefix) or len(key) > 300 or " " in key:
            raise AiKeyError(f"That does not look like a {self.company} API key (it starts with {self.key_prefix}).")

    def check(self, key: str) -> list[str]:
        raise NotImplementedError

    def default_model(self, models: list[str]) -> str:
        return models[0] if models else ""

    def label(self, model: str) -> str:
        return model

    def ask(self, key: str, model: str, system: str, turns: list[Turn]) -> Reply:
        raise NotImplementedError

    # shared by the HTTP providers
    def _http_error(self, r: requests.Response) -> AiKeyError:
        try:
            body = r.json()
            msg = (body.get("error") or {}).get("message") or str(body)[:300]
        except ValueError:
            msg = r.text[:300]
        low = msg.lower()
        if r.status_code in (401, 403) or "api key not valid" in low or "api_key_invalid" in low or "incorrect api key" in low:
            return AiKeyError(f"{self.company} refused this API key. Check it in Settings > AI assistant.")
        if r.status_code == 404:
            return AiKeyError("That model is not available for this API key. Choose another one in Settings.")
        if r.status_code == 429:
            if "quota" in low or "billing" in low or "credit" in low:
                return AiKeyError(f"Your {self.company} account has no quota or credit left for this model.")
            return AiKeyError(f"Your {self.company} account hit its rate limit. Wait a minute and try again.")
        if r.status_code >= 500:
            return AiKeyError(f"{self.company} had a problem ({r.status_code}). Try again in a moment.")
        return AiKeyError(f"{self.company} could not do it: {msg}")

    def _request(self, method: str, url: str, **kw) -> dict:
        try:
            r = requests.request(method, url, timeout=kw.pop("timeout", TIMEOUT), **kw)
        except requests.RequestException as exc:
            raise AiKeyError(f"Could not reach {self.company}. Check the server's internet connection.") from exc
        if not r.ok:
            raise self._http_error(r)
        return r.json()


# ---------------------------------------------------------------- Claude
CLAUDE_MODELS = [
    ("claude-opus-5-5", "Claude Opus 5.5 · best results (default)"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5 · faster, cheaper"),
    ("claude-haiku-5-5", "Claude Haiku 5.5 · fastest, cheapest"),
]
CLAUDE_FALLBACK = {"claude-opus-5-5", "claude-sonnet-5-5"}   # server-side refusal fallback is available on these


def _anthropic_error(exc: Exception) -> AiKeyError:
    if isinstance(exc, anthropic.AuthenticationError):
        return AiKeyError("Anthropic refused this API key. Check it in Settings > AI assistant.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return AiKeyError("This API key is not allowed to use that model.")
    if isinstance(exc, anthropic.NotFoundError):
        return AiKeyError("That model is not available for this API key. Choose another one in Settings.")
    if isinstance(exc, anthropic.RateLimitError):
        return AiKeyError("Your Anthropic account hit its rate limit. Wait a minute and try again.")
    if isinstance(exc, anthropic.BadRequestError):
        msg = str(getattr(exc, "message", exc))
        if "credit" in msg.lower() or "billing" in msg.lower():
            return AiKeyError("Your Anthropic account has no credit left. Add credit in the Anthropic Console.")
        return AiKeyError(f"Anthropic could not use this file: {msg}")
    if isinstance(exc, anthropic.APIStatusError):
        return AiKeyError(f"Anthropic had a problem ({exc.status_code}). Try again in a moment.")
    if isinstance(exc, anthropic.APIConnectionError):
        return AiKeyError("Could not reach Anthropic. Check the server's internet connection.")
    return AiKeyError(str(exc))


class Claude(Provider):
    id, name, company, key_prefix = "anthropic", "Claude", "Anthropic", "sk-ant-"
    placeholder = "sk-ant-…"
    console_url, console_name = "https://console.anthropic.com/settings/keys", "Anthropic Console"
    note = "Paid per use. Reads PDFs, Word, PowerPoint and text."

    def check(self, key: str) -> list[str]:
        try:   # free call: checks the key without spending tokens
            anthropic.Anthropic(api_key=key, max_retries=1, timeout=20).models.list(limit=1)
        except anthropic.APIError as exc:
            raise _anthropic_error(exc) from exc
        return [m for m, _ in CLAUDE_MODELS]

    def label(self, model: str) -> str:
        return dict(CLAUDE_MODELS).get(model, model)

    def ask(self, key: str, model: str, system: str, turns: list[Turn]) -> Reply:
        client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=900)
        messages = []
        for t in turns:
            content: list[dict] = []
            if t.pdf is not None:
                content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                               "data": base64.standard_b64encode(t.pdf).decode()}})
            content.append({"type": "text", "text": t.text})
            messages.append({"role": t.role, "content": content})
        kwargs = dict(model=model, max_tokens=64000, system=[{"type": "text", "text": system}], messages=messages,
                      output_config={"effort": "medium"})
        if model in CLAUDE_FALLBACK:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            with client.beta.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except anthropic.APIError as exc:
            raise _anthropic_error(exc) from exc
        if message.stop_reason == "refusal":
            raise AiKeyError("Claude declined to convert this content.")
        if message.stop_reason == "max_tokens":
            raise AiKeyError("The note is too long to convert in one go: split the file into smaller parts.")
        return Reply(text="".join(b.text for b in message.content if b.type == "text"), model=message.model)


# ---------------------------------------------------------------- Gemini
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_SKIP = ("embedding", "image", "tts", "audio", "live", "aqa", "robotics", "computer-use", "native", "learnlm", "gemma")


def _gemini_rank(model: str) -> tuple:
    """Newest version first; pro before flash before lite; stable before preview."""
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", model)
    version = float(m.group(1)) if m else 0.0
    tier = 0 if "-pro" in model else 2 if "lite" in model else 1
    unstable = any(w in model for w in ("preview", "exp", "latest"))
    return (-version, tier, unstable, model)


class Gemini(Provider):
    id, name, company, key_prefix = "google", "Gemini", "Google", "AIza"
    placeholder = "AIza…"
    console_url, console_name = "https://aistudio.google.com/apikey", "Google AI Studio"
    note = "Has a free tier (with limits). Reads PDFs, Word, PowerPoint and text."

    def _headers(self, key: str) -> dict:
        return {"x-goog-api-key": key, "Content-Type": "application/json"}

    def check(self, key: str) -> list[str]:
        data = self._request("GET", f"{GEMINI_URL}/models", headers=self._headers(key), params={"pageSize": 1000}, timeout=20)
        ids = [m["name"].removeprefix("models/") for m in data.get("models", [])
               if "generateContent" in m.get("supportedGenerationMethods", [])]
        ids = [i for i in ids if i.startswith("gemini") and not any(w in i for w in _GEMINI_SKIP)]
        if not ids:
            raise AiKeyError("This Google key cannot use any Gemini model. Check it in Google AI Studio.")
        return sorted(ids, key=_gemini_rank)

    def ask(self, key: str, model: str, system: str, turns: list[Turn]) -> Reply:
        contents = []
        for t in turns:
            parts: list[dict] = []
            if t.pdf is not None:
                parts.append({"inline_data": {"mime_type": "application/pdf", "data": base64.standard_b64encode(t.pdf).decode()}})
            parts.append({"text": t.text})
            contents.append({"role": "model" if t.role == "assistant" else "user", "parts": parts})
        data = self._request("POST", f"{GEMINI_URL}/models/{model}:generateContent", headers=self._headers(key),
                             json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents})
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise AiKeyError(f"Gemini declined to convert this content ({block.lower()}).")
        cand = (data.get("candidates") or [{}])[0]
        reason = cand.get("finishReason", "")
        if reason == "MAX_TOKENS":
            raise AiKeyError("The note is too long to convert in one go: split the file into smaller parts.")
        if reason in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            raise AiKeyError(f"Gemini declined to convert this content ({reason.lower()}).")
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        return Reply(text=text, model=data.get("modelVersion") or model)


# ---------------------------------------------------------------- OpenAI
OPENAI_URL = "https://api.openai.com/v1"
_OPENAI_SKIP = ("audio", "realtime", "tts", "transcribe", "image", "search", "embedding", "instruct", "moderation",
                "dall-e", "whisper", "codex", "computer-use", "babbage", "davinci", "deep-research")


class OpenAI(Provider):
    id, name, company, key_prefix = "openai", "OpenAI", "OpenAI", "sk-"
    placeholder = "sk-…"
    console_url, console_name = "https://platform.openai.com/api-keys", "OpenAI Platform"
    note = "Paid per use (a ChatGPT subscription does not include API use). Reads PDFs, Word, PowerPoint and text."

    def check_format(self, key: str) -> None:
        if key.startswith("sk-ant-"):
            raise AiKeyError("That is an Anthropic key: save it under Claude.")
        super().check_format(key)

    def _headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def check(self, key: str) -> list[str]:
        data = self._request("GET", f"{OPENAI_URL}/models", headers=self._headers(key), timeout=20)
        rows = [m for m in data.get("data", []) if re.match(r"^(gpt-|o\d)", m.get("id", ""))
                and not any(w in m["id"] for w in _OPENAI_SKIP)]
        if not rows:
            raise AiKeyError("This OpenAI key cannot use any chat model. Check it on the OpenAI Platform.")
        rows.sort(key=lambda m: -int(m.get("created") or 0))   # newest first
        return [m["id"] for m in rows]

    def default_model(self, models: list[str]) -> str:
        """The newest full GPT model (not mini / nano, not a dated snapshot), else the newest."""
        for m in models:
            if m.startswith("gpt-") and not re.search(r"mini|nano|\d{4}-\d{2}-\d{2}|chat-latest", m):
                return m
        return models[0] if models else ""

    def ask(self, key: str, model: str, system: str, turns: list[Turn]) -> Reply:
        messages: list[dict] = [{"role": "system", "content": system}]
        for t in turns:
            if t.role == "assistant":
                messages.append({"role": "assistant", "content": t.text})
                continue
            content: list[dict] = []
            if t.pdf is not None:
                content.append({"type": "file", "file": {"filename": "notes.pdf",
                                "file_data": "data:application/pdf;base64," + base64.standard_b64encode(t.pdf).decode()}})
            content.append({"type": "text", "text": t.text})
            messages.append({"role": "user", "content": content})
        data = self._request("POST", f"{OPENAI_URL}/chat/completions", headers=self._headers(key),
                             json={"model": model, "messages": messages})
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        if msg.get("refusal"):
            raise AiKeyError("OpenAI declined to convert this content.")
        if choice.get("finish_reason") == "length":
            raise AiKeyError("The note is too long to convert in one go: split the file into smaller parts.")
        if choice.get("finish_reason") == "content_filter":
            raise AiKeyError("OpenAI declined to convert this content.")
        return Reply(text=msg.get("content") or "", model=data.get("model") or model)


PROVIDERS: dict[str, Provider] = {p.id: p for p in (Claude(), Gemini(), OpenAI())}
DEFAULT_PROVIDER = "anthropic"
