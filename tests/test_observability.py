"""Ops endpoints: probes, version and Prometheus metrics (no network, no key)."""

from tests.test_server import client_for


def test_probes_and_version():
    c = client_for(False)
    assert c.get("/healthz").status_code == 200
    assert c.get("/readyz").json()["ready"] is True
    assert set(c.get("/version").json()) == {"version", "commit", "build_date"}


def test_readyz_fails_when_config_is_broken(tmp_path):
    bad = tmp_path / "agent.yaml"
    bad.write_text("version: 0\n")
    c = client_for(False, config_path=bad)
    assert c.get("/readyz").status_code == 503
    assert c.get("/healthz").status_code == 200  # liveness stays up: a restart would not fix config


def test_metrics_use_route_templates_not_raw_paths():
    c = client_for(True)
    sid = c.post("/api/session").json()["session_id"]
    c.post(f"/api/session/{sid}/message", json={"text": "I have crushing chest pain"})
    body = c.get("/metrics").text
    assert 'route="/api/session/{session_id}/message"' in body
    assert sid not in body
    assert 'yourhealth_turns_total{outcome="emergency_gate"}' in body
    assert "yourhealth_build_info" in body
