"""Sentence-level streaming through the output guard.

Tokens from the model are buffered until a sentence ends; each complete sentence is checked by the
guard and only then sent to the patient. A sentence that fails is never shown: streaming stops and
the caller appends the guard's safe reply. So the patient sees text appear progressively, but never
a token the guard has not approved (the same pattern a voice agent uses before text-to-speech).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from types import SimpleNamespace

from .guard import Verdict, check_reply
from .tools import Session

# A sentence ends at . ! ? followed by whitespace, except after common titles ("Dr. Rao").
_BOUNDARY = re.compile(r"(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<!\bSt)[.!?](?=\s)")


class SentenceStreamer:
    """Accumulates streamed text and emits guard-approved sentences."""

    def __init__(self, session: Session, emit: Callable[[str], None]):
        self.session = session
        self.emit = emit
        self.buffer = ""
        self.shown: list[str] = []
        self.blocked: Verdict | None = None

    def feed(self, text: str) -> bool:
        """Add streamed text. Returns False once a sentence has been blocked."""
        self.buffer += text
        while self.blocked is None and (m := _BOUNDARY.search(self.buffer)):
            sentence, self.buffer = self.buffer[: m.end()].strip(), self.buffer[m.end() :]
            self._release(sentence)
        return self.blocked is None

    def flush(self) -> bool:
        """Release whatever is left (end of a model message)."""
        rest, self.buffer = self.buffer.strip(), ""
        if rest and self.blocked is None:
            self._release(rest)
        return self.blocked is None

    @property
    def text(self) -> str:
        return " ".join(self.shown)

    def _release(self, sentence: str) -> None:
        verdict = check_reply(sentence, self.session)
        if not verdict.ok:
            self.blocked = verdict
            return
        self.shown.append(sentence)
        self.emit(sentence)


def read_stream(chunks: Iterable, streamer: SentenceStreamer, on_usage: Callable[[object], None]):
    """Consume a Chat Completions stream. Text goes through the streamer; tool calls are reassembled.

    Returns a message-like object (content, tool_calls) matching the non-streaming API, so the agent
    loop handles both the same way. Stops reading as soon as the streamer blocks a sentence.
    """
    content, calls = [], {}
    for chunk in chunks:
        if getattr(chunk, "usage", None):
            on_usage(chunk.usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        for tc in getattr(delta, "tool_calls", None) or []:
            slot = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
            slot["id"] = tc.id or slot["id"]
            if tc.function is not None:
                slot["name"] += tc.function.name or ""
                slot["arguments"] += tc.function.arguments or ""
        if getattr(delta, "content", None):
            content.append(delta.content)
            if not streamer.feed(delta.content):
                close = getattr(chunks, "close", None)
                if close:
                    close()
                break
    tool_calls = [
        SimpleNamespace(id=c["id"], function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
        for _, c in sorted(calls.items())
    ]
    return SimpleNamespace(content="".join(content) or None, tool_calls=tool_calls or None)
