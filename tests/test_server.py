"""Web API tests with a scripted LLM: no network, no key."""

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
    c = client_for(False, reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
                   reply("Thanks Priya."))
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
    book = [reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
            reply(calls=[tool_call("propose_booking", slot_id="P1-20261013-0930", reason="checkup")]),
            reply("Confirm Tuesday 9:30?"),
            reply(calls=[tool_call("confirm_pending")]),
            reply("Booked.")]
    c = client_for(True, *book, reply(calls=[tool_call("verify_patient", full_name="Tom Becker", date_of_birth="1979-09-09")]),
                   reply(calls=[tool_call("propose_booking", slot_id="P1-20261013-0930", reason="flu")]), reply("Sorry."))
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
