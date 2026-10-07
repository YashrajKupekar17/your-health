"""Sentence-level streaming: text arrives progressively, but only guard-approved sentences are sent."""

from types import SimpleNamespace as NS

from tests.test_agent import reply, tool_call
from yourhealth.agent import Agent
from yourhealth.guard import SAFE_CLAIM_REPLY


def to_chunks(scripted, piece=6):
    """Turn a scripted non-streaming reply into Chat Completions stream chunks."""
    msg = scripted.choices[0].message
    for i, c in enumerate(msg.tool_calls or []):
        fn = NS(name=c.function.name, arguments=c.function.arguments)
        yield NS(usage=None, choices=[NS(delta=NS(content=None, tool_calls=[NS(index=i, id=c.id, function=fn)]))])
    text = msg.content or ""
    for k in range(0, len(text), piece):
        yield NS(usage=None, choices=[NS(delta=NS(content=text[k : k + piece], tool_calls=None))])
    yield NS(usage=NS(prompt_tokens=10, completion_tokens=5), choices=[])


class ClosableStream:
    def __init__(self, chunks):
        self._it, self.closed, self.consumed = iter(chunks), False, 0

    def __iter__(self):
        return self

    def __next__(self):
        self.consumed += 1
        return next(self._it)

    def close(self):
        self.closed = True


class FakeStreamLLM:
    def __init__(self, *responses):
        self.responses, self.streams = list(responses), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        assert kwargs.get("stream") is True and kwargs["stream_options"] == {"include_usage": True}
        stream = ClosableStream(list(to_chunks(self.responses.pop(0))))
        self.streams.append(stream)
        return stream


def run(*responses, text="hi"):
    a = Agent(client=FakeStreamLLM(*responses))
    deltas, labels = [], []
    out = a.respond(text, on_progress=labels.append, on_delta=deltas.append)
    return a, out, deltas, labels


def test_reply_arrives_sentence_by_sentence():
    _, out, deltas, _ = run(reply("Hello there. I have 9:00, 9:30 or 10:00 with Dr. Rao on Monday. Which works?"))
    assert deltas == ["Hello there.", "I have 9:00, 9:30 or 10:00 with Dr. Rao on Monday.", "Which works?"]
    assert out == " ".join(deltas)


def test_tools_then_streamed_reply_with_progress_and_usage():
    a, out, deltas, labels = run(
        reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
        reply("Thanks Priya. What would you like to book?"),
    )
    assert labels == ["Verifying your details…"]
    assert deltas == ["Thanks Priya.", "What would you like to book?"]
    assert a.session.patient_id == "PT1"
    assert a._usage == {"llm_calls": 2, "prompt_tokens": 20, "completion_tokens": 10}


def test_false_claim_is_never_streamed():
    a, out, deltas, _ = run(reply("Thanks Priya. I've booked you for Tuesday. See you then!"))
    assert deltas == ["Thanks Priya.", SAFE_CLAIM_REPLY]  # the claim and everything after it never left
    assert "booked you" not in out and "See you then" not in out
    assert a.client.streams[0].closed  # stopped reading the model's stream


def test_claim_after_a_real_confirm_streams_normally():
    book = [
        reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
        reply(calls=[tool_call("propose_booking", slot_id="P1-20261013-0930", reason="checkup")]),
        reply("Shall I book Tuesday 13 October at 9:30?"),
        reply(calls=[tool_call("confirm_pending")]),
        reply("Done. I've booked you for Tuesday at 9:30."),
    ]
    a = Agent(client=FakeStreamLLM(*book))
    a.respond("Priya Sharma 1990-04-12, Tuesday 9:30 please", on_delta=lambda _: None)
    deltas = []
    a.respond("yes", on_delta=deltas.append)
    assert deltas == ["Done.", "I've booked you for Tuesday at 9:30."]


def test_text_before_a_tool_call_is_not_glued_to_the_next_message():
    """Regression: 'One moment please.' (no trailing space) then a tool call, then more text."""
    pre = reply("One moment please.", calls=[tool_call("list_providers", specialty=None)])
    _, out, deltas, labels = run(pre, reply("We have four providers."))
    assert deltas == ["One moment please.", "We have four providers."]
    assert out == "One moment please. We have four providers."
    assert labels == ["Looking up our providers…"]
