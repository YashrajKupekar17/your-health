"""Small web UI: chat with the agent and watch what happens behind the scenes.

In-memory sessions only (this is a demo, not a deployment): each browser session gets its own
Agent and its own copy of the clinic, capped and evicted oldest-first.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent, load_config
from .clinic import Clinic

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MAX_SESSIONS = 50
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="YourHealth scheduling agent")
_sessions: OrderedDict[str, Agent] = OrderedDict()
_lock = threading.Lock()


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _state(agent: Agent) -> dict:
    s, clinic = agent.session, agent.session.clinic
    patient = clinic.patients.get(s.patient_id) if s.patient_id else None
    return {
        "turn": s.turn,
        "verified_patient": patient["name"] if patient else None,
        "failed_verifications": s.failed_verifications,
        "pending": s.pending.summary if s.pending else None,
        "handoff": s.handoff,
        "appointments": clinic.upcoming_appointments(s.patient_id) if s.patient_id else [],
    }


def _get(session_id: str) -> Agent:
    with _lock:
        agent = _sessions.get(session_id)
    if agent is None:
        raise HTTPException(404, "session not found; start a new conversation")
    return agent


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
def info() -> dict:
    config = load_config()
    clinic = Clinic.load()
    return {
        "clinic": clinic.name,
        "now": f"{clinic.now:%A %d %B %Y, %H:%M}",
        "config_version": config["version"],
        "learned_rules": [r["rule"] for r in config.get("learned_rules") or []],
        "patients": [{"name": p["name"], "dob": p["dob"]} for p in clinic.patients.values()],
        "providers": clinic.list_providers(),
    }


@app.post("/api/session")
def new_session() -> dict:
    agent = Agent()
    sid = uuid.uuid4().hex
    with _lock:
        _sessions[sid] = agent
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
    return {"session_id": sid, "greeting": agent.greeting, "state": _state(agent)}


@app.post("/api/session/{session_id}/message")
def send(session_id: str, msg: Message) -> dict:
    agent = _get(session_id)
    with agent.lock:  # one message at a time per conversation
        seen = len(agent.session.tool_log)
        reply = agent.respond(msg.text.strip())
        tools = agent.session.tool_log[seen:]
    return {"reply": reply, "tools": tools, "state": _state(agent), "ended": agent.session.ended}


def main() -> None:
    import uvicorn

    uvicorn.run("yourhealth.server:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
