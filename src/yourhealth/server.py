"""Web API + UI for the agent.

Built by `create_app()` so tests (and a real deployment) inject settings, the LLM client, the
clinic and the session store. One clinic instance is shared by all conversations: it stands in for
the practice's scheduling system, so two patients really compete for the same slots.

DEMO_MODE=1 exposes internals for the walkthrough (tool calls, session state, the demo patient
list). Without it, the API returns only the reply: tool results, appointment details and the
patient list never leave the server.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .agent import Agent, load_config, make_client
from .clinic import Clinic
from .logs import configure_logging, get_logger, log_event
from .sessions import InMemorySessionStore, SessionStore
from .settings import Settings, get_settings, load_env

STATIC = Path(__file__).resolve().parent / "static"
log = get_logger("server")


class Message(BaseModel):
    text: str


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


def create_app(
    settings: Settings | None = None, client=None, clinic: Clinic | None = None, store: SessionStore | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    clinic = clinic or Clinic.load(settings.clinic_data_path)
    store = store or InMemorySessionStore(settings.max_sessions, settings.session_idle_ttl_s)
    llm = {"client": client}  # created on first use: one connection pool for every conversation

    def get_client():
        if llm["client"] is None:
            llm["client"] = make_client(settings)
        return llm["client"]

    app = FastAPI(title="YourHealth scheduling agent")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "sessions": len(store)}

    @app.get("/api/info")
    def info() -> dict:
        out = {"clinic": clinic.name, "demo_mode": settings.demo_mode}
        if settings.demo_mode:
            config = load_config(settings.config_path)
            out |= {
                "now": f"{clinic.now:%A %d %B %Y, %H:%M}",
                "config_version": config["version"],
                "learned_rules": [r["rule"] for r in config["learned_rules"]],
                "patients": [{"name": p["name"], "dob": p["dob"]} for p in clinic.patients.values()],
            }
        return out

    @app.post("/api/session")
    def new_session() -> dict:
        # Config is read per conversation: a new version from the loop applies to new chats only.
        agent = Agent(config=load_config(settings.config_path), clinic=clinic, client=get_client(), settings=settings)
        sid = store.put(agent)
        log_event(log, "session_created", session=sid, config_version=agent.config["version"])
        out = {"session_id": sid, "greeting": agent.greeting}
        if settings.demo_mode:
            out["state"] = _state(agent)
        return out

    @app.post("/api/session/{session_id}/message")
    def send(session_id: str, msg: Message) -> dict:
        text = msg.text.strip()
        if not text or len(text) > settings.max_message_chars:
            raise HTTPException(422, f"message must be 1-{settings.max_message_chars} characters")
        agent = store.get(session_id)
        if agent is None:
            raise HTTPException(404, "session not found or expired; start a new conversation")
        started = time.perf_counter()
        with agent.lock:  # one message at a time per conversation
            seen = len(agent.session.tool_log)
            reply = agent.respond(text)
            tools = agent.session.tool_log[seen:]
        log_event(log, "message", session=session_id, ms=round((time.perf_counter() - started) * 1000, 1))
        out = {"reply": reply, "ended": agent.session.ended}
        if settings.demo_mode:
            out |= {"tools": tools, "state": _state(agent)}
        return out

    return app


def main() -> None:
    import uvicorn

    load_env()
    settings = get_settings()
    uvicorn.run("yourhealth.server:create_app", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
