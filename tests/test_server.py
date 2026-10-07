"""Web API tests with a scripted LLM: no network, no key."""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from tests.test_agent import FakeLLM, reply, tool_call
from yourhealth.server import create_app
from yourhealth.sessions import InMemorySessionStore
from yourhealth.settings import Settings


def client_for(demo: bool, *responses, **settings):
    app = create_app(Settings(demo_mode=demo, **settings), client=FakeLLM(*responses))
    return TestClient(app)


def start(c):
    return c.post("/api/session").json()["session_id"]


def test_health_and_index():
    c = client_for(True)
    assert c.get("/healthz").json()["ok"] is True
    assert "YourHealth" in c.get("/").text


def test_demo_mode_exposes_internals():
    c = client_for(True)
    info = c.get("/api/info").json()
    assert info["demo_mode"] and len(info["patients"]) == 6
    r = c.post(f"/api/session/{start(c)}/message", json={"text": "I have crushing chest pain"}).json()
    assert r["ended"] and r["state"]["handoff"]["urgent"] and r["tools"][0]["tool"] == "handoff_to_human"


def test_production_mode_returns_only_the_reply():
    c = client_for(
        False,
        reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
        reply("Thanks Priya."),
    )
    info = c.get("/api/info").json()
    assert set(info) == {"clinic", "demo_mode"}  # no patient list
    r = c.post(f"/api/session/{start(c)}/message", json={"text": "Priya Sharma 1990-04-12"}).json()
    assert set(r) == {"reply", "ended"}  # no tool results, no appointments


def test_bad_requests():
    c = client_for(True, max_message_chars=10)
    assert c.post("/api/session/nope/message", json={"text": "hi"}).status_code == 404
    sid = start(c)
    assert c.post(f"/api/session/{sid}/message", json={"text": "   "}).status_code == 422
    assert c.post(f"/api/session/{sid}/message", json={"text": "x" * 11}).status_code == 422


def test_conversations_share_one_schedule():
    """A slot booked in one conversation is gone for the next: the clinic is shared, not copied."""
    book = [
        reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
        reply(calls=[tool_call("propose_booking", slot_id="P1-20261013-0930", reason="checkup")]),
        reply("Confirm Tuesday 9:30?"),
        reply(calls=[tool_call("confirm_pending")]),
        reply("Booked."),
    ]
    c = client_for(
        True,
        *book,
        reply(calls=[tool_call("verify_patient", full_name="Tom Becker", date_of_birth="1979-09-09")]),
        reply(calls=[tool_call("propose_booking", slot_id="P1-20261013-0930", reason="flu")]),
        reply("Sorry."),
    )
    first = start(c)
    c.post(f"/api/session/{first}/message", json={"text": "book 9:30 tue"})
    c.post(f"/api/session/{first}/message", json={"text": "yes"})
    r = c.post(f"/api/session/{start(c)}/message", json={"text": "Tom, 1979-09-09, tuesday 9:30"}).json()
    assert [t["result"].get("error") for t in r["tools"]][-1] == "slot_unavailable"


# ---- session store ----------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def agent_factory():
    from yourhealth.agent import Agent

    return lambda: Agent(client=FakeLLM())


def test_store_expires_idle_sessions(agent_factory):
    clock = Clock()
    store = InMemorySessionStore(max_sessions=10, idle_ttl_s=60, clock=clock)
    a = agent_factory()
    sid = store.put(a)
    clock.t = 50
    assert store.get(sid) is a  # touching refreshes the TTL
    clock.t = 100
    assert store.get(sid) is a
    clock.t = 161
    assert store.get(sid) is None


def test_store_evicts_least_recently_used(agent_factory):
    store = InMemorySessionStore(max_sessions=2, idle_ttl_s=1e9)
    a, b, c = agent_factory(), agent_factory(), agent_factory()
    sa, sb = store.put(a), store.put(b)
    store.get(sa)  # a is now most recent
    store.put(c)
    assert store.get(sb) is None and store.get(sa) is a and len(store) == 2


# ---- streaming --------------------------------------------------------------------


def parse_sse(body: str) -> list[tuple[str, dict]]:
    import json

    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def stream_client(*responses):
    from tests.test_streaming import FakeStreamLLM

    return TestClient(create_app(Settings(demo_mode=False), client=FakeStreamLLM(*responses)))


def test_stream_sends_progress_then_done():
    c = stream_client(
        reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
        reply(calls=[tool_call("list_my_appointments")]),
        reply("You have one appointment on Tuesday 20 October."),
    )
    with c.stream("POST", f"/api/session/{start(c)}/message/stream", json={"text": "Priya Sharma 1990-04-12"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.read().decode())
    assert [e for e, _ in events] == ["progress", "progress", "delta", "done"]
    assert events[0][1]["label"] == "Verifying your details…"
    assert events[-1][1] == {"reply": "You have one appointment on Tuesday 20 October.", "ended": False}


def test_stream_done_reply_is_guarded():
    c = stream_client(reply("I've booked you in!"))
    with c.stream("POST", f"/api/session/{start(c)}/message/stream", json={"text": "book me"}) as r:
        events = parse_sse(r.read().decode())
    safe = "Sorry, I haven't made any change yet. Would you like me to go ahead?"
    assert events == [("delta", {"text": safe}), ("done", {"reply": safe, "ended": False})]


def test_stream_validates_like_the_plain_endpoint():
    c = client_for(False)
    assert c.post("/api/session/nope/message/stream", json={"text": "hi"}).status_code == 404


def test_stream_sends_reply_sentences_as_deltas():
    c = stream_client(reply("Hello. How can I help?"))
    with c.stream("POST", f"/api/session/{start(c)}/message/stream", json={"text": "hi"}) as r:
        events = parse_sse(r.read().decode())
    assert events == [
        ("delta", {"text": "Hello."}),
        ("delta", {"text": "How can I help?"}),
        ("done", {"reply": "Hello. How can I help?", "ended": False}),
    ]


# ---- voice input ------------------------------------------------------------------


class FakeTranscriber:
    def __init__(self, text="I'd like to book a checkup with Dr Rao", fail=False):
        self.calls = []
        self.fail, self.text = fail, text
        self.audio = NS(transcriptions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("provider down")
        return NS(text=f"  {self.text}  ")


def voice_client(**kw):
    t = FakeTranscriber(**kw)
    return TestClient(create_app(Settings(demo_mode=False, max_audio_bytes=100), client=t)), t


def test_transcribe_returns_text_and_biases_vocabulary():
    c, t = voice_client()
    r = c.post("/api/transcribe", content=b"fake-webm-bytes", headers={"Content-Type": "audio/webm;codecs=opus"})
    assert r.status_code == 200 and r.json() == {"text": "I'd like to book a checkup with Dr Rao"}
    call = t.calls[0]
    assert call["file"][0] == "speech.webm" and "Dr. Anita Rao" in call["prompt"]


def test_transcribe_rejects_bad_input():
    c, _ = voice_client()
    assert c.post("/api/transcribe", content=b"x", headers={"Content-Type": "text/plain"}).status_code == 415
    assert c.post("/api/transcribe", content=b"", headers={"Content-Type": "audio/webm"}).status_code == 422
    assert c.post("/api/transcribe", content=b"x" * 101, headers={"Content-Type": "audio/webm"}).status_code == 413


def test_transcribe_provider_failure_is_a_clean_error():
    c, _ = voice_client(fail=True)
    r = c.post("/api/transcribe", content=b"audio", headers={"Content-Type": "audio/webm"})
    assert r.status_code == 502 and "type" in r.json()["detail"]
