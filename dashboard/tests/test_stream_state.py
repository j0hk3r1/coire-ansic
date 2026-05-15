from fastapi.testclient import TestClient
import app


def test_stream_state_returns_all_sections(monkeypatch):
    monkeypatch.setattr(app, "bifrost_get", lambda *a, **k: {"logs": [], "providers": [], "rules": []})
    monkeypatch.setattr(app, "load_circuit_breaker", lambda: {"demoted": [], "demoted_count": 0})
    monkeypatch.setattr(app, "load_curator_recommendations", lambda: {"best": [], "code": [], "mid": [], "fast": [], "total_models": 0})
    monkeypatch.setattr(app, "load_curator_history", lambda n=30: {"events": []})
    monkeypatch.setattr(app, "get_cron_status", lambda: {"jobs": {}})

    client = TestClient(app.app)
    r = client.get("/api/stream_state?h=24")
    assert r.status_code == 200
    j = r.json()
    for key in [
        "pool_health", "circuit_breaker", "pool_targets", "provider_status",
        "bifrost_metrics", "provider_errors", "recent_errors", "recent_successes",
        "curator_recommendations", "curator_history", "activity_heatmap",
        "requests_per_minute", "cron_status", "now", "server_ts",
    ]:
        assert key in j, f"missing key {key}"
