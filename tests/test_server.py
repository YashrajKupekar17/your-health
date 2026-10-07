"""Web API smoke tests. The emergency path never calls the LLM, so no real key is needed."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-used")
    from yourhealth.server import app
    return TestClient(app)


def test_index_and_info(client):
    assert "YourHealth" in client.get("/").text
    info = client.get("/api/info").json()
    assert info["config_version"] >= 1 and len(info["patients"]) == 6


def test_emergency_turn_hands_off(client):
    sid = client.post("/api/session").json()["session_id"]
    r = client.post(f"/api/session/{sid}/message", json={"text": "I have crushing chest pain"}).json()
    assert r["ended"] and r["state"]["handoff"]["urgent"]
    assert r["tools"][0]["tool"] == "handoff_to_human"


def test_bad_requests(client):
    assert client.post("/api/session/nope/message", json={"text": "hi"}).status_code == 404
    sid = client.post("/api/session").json()["session_id"]
    assert client.post(f"/api/session/{sid}/message", json={"text": ""}).status_code == 422
