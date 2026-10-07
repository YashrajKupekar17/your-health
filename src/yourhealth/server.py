"""Web API + UI for the agent.

Built by `create_app()` so tests (and a real deployment) inject settings, the LLM client, the
clinic and the session store. One clinic instance is shared by all conversations: it stands in for
the practice's scheduling system, so two patients really compete for the same slots.

DEMO_MODE=1 exposes internals for the walkthrough (tool calls, session state, the demo patient
list). Without it, the API returns only the reply: tool results, appointment details and the
patient list never leave the server.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
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

    audio_types = {
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mp4": "mp4",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
    }
    # Bias the transcriber toward words that matter here: provider names and identity details are
    # exactly what speech-to-text gets wrong, and a misheard DOB fails verification.
    vocab = (
        "A patient calling a clinic to book, move or cancel an appointment. Providers: "
        + ", ".join(p["name"] for p in clinic.list_providers())
        + ". The caller may give their full name and date of birth."
    )

    @app.post("/api/transcribe")
    async def transcribe(request: Request) -> dict:
        """Raw audio body (e.g. audio/webm from the browser's MediaRecorder) -> {"text": transcript}."""
        kind = request.headers.get("content-type", "").split(";")[0].strip()
        if kind not in audio_types:
            raise HTTPException(415, f"unsupported audio type {kind!r}")
        audio = await request.body()
        if not audio:
            raise HTTPException(422, "empty recording")
        if len(audio) > settings.max_audio_bytes:
            raise HTTPException(413, "recording too long")
        started = time.perf_counter()
        try:
            result = await run_in_threadpool(
                get_client().audio.transcriptions.create,
                model=settings.transcribe_model,
                file=(f"speech.{audio_types[kind]}", audio, kind),
                prompt=vocab,
            )
        except Exception as e:  # noqa: BLE001 - report a clean error; the patient can type instead
            log_event(log, "transcribe_error", error_type=type(e).__name__)
            raise HTTPException(502, "could not transcribe that; please try again or type") from None
        text = (getattr(result, "text", "") or "").strip()
        log_event(
            log, "transcribe", bytes=len(audio), chars=len(text), ms=round((time.perf_counter() - started) * 1000, 1)
        )
        return {"text": text}

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

    def _accept(session_id: str, msg: Message) -> tuple[Agent, str]:
        text = msg.text.strip()
        if not text or len(text) > settings.max_message_chars:
            raise HTTPException(422, f"message must be 1-{settings.max_message_chars} characters")
        agent = store.get(session_id)
        if agent is None:
            raise HTTPException(404, "session not found or expired; start a new conversation")
        return agent, text

    def _handle(agent: Agent, text: str, on_progress=None, on_delta=None) -> dict:
        started = time.perf_counter()
        with agent.lock:  # one message at a time per conversation
            seen = len(agent.session.tool_log)
            reply = agent.respond(text, on_progress=on_progress, on_delta=on_delta)
            tools = agent.session.tool_log[seen:]
        log_event(log, "message", session=agent.session.id, ms=round((time.perf_counter() - started) * 1000, 1))
        out = {"reply": reply, "ended": agent.session.ended}
        if settings.demo_mode:
            out |= {"tools": tools, "state": _state(agent)}
        return out

    @app.post("/api/session/{session_id}/message")
    def send(session_id: str, msg: Message) -> dict:
        return _handle(*_accept(session_id, msg))

    @app.post("/api/session/{session_id}/message/stream")
    def send_stream(session_id: str, msg: Message) -> StreamingResponse:
        """Server-sent events: `progress` while tools run, `delta` per reply sentence, then `done`.

        Each sentence is approved by the output guard before it is sent, so streaming never shows
        the patient an unchecked claim. `done` carries the full reply as the source of truth.
        """
        agent, text = _accept(session_id, msg)
        events: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                result = _handle(
                    agent,
                    text,
                    on_progress=lambda label: events.put(("progress", {"label": label})),
                    on_delta=lambda sentence: events.put(("delta", {"text": sentence})),
                )
                events.put(("done", result))
            except Exception as e:  # noqa: BLE001 - surface as an SSE error event instead of a dropped stream
                log_event(log, "stream_error", session=agent.session.id, error_type=type(e).__name__)
                events.put(("error", {"detail": "something went wrong; please try again"}))
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while (item := events.get()) is not None:
                event, data = item
                yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    import uvicorn

    load_env()
    settings = get_settings()
    uvicorn.run("yourhealth.server:create_app", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
