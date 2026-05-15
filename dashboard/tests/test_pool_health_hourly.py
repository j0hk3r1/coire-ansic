import app
from freezegun import freeze_time


@freeze_time("2026-05-08T13:00:00Z")
def test_pool_health_includes_hourly_p50(monkeypatch):
    monkeypatch.setattr(app, "bifrost_get", lambda *a, **k: {
        "logs": [
            {"timestamp": "2026-05-08T12:00:00.000Z", "routing_rule_name": "best",
             "provider": "groq", "model": "x", "status": "success", "latency": 1000},
            {"timestamp": "2026-05-08T12:30:00.000Z", "routing_rule_name": "best",
             "provider": "groq", "model": "x", "status": "success", "latency": 2000},
        ]
    })
    out = app.load_pool_health(window_hours=24)
    p = out["pools"][0]
    assert "hourly_p50" in p
    assert isinstance(p["hourly_p50"], list)
    assert len(p["hourly_p50"]) == 24
    # hour0 = now - (window_hours-1)h = 2026-05-07T14:00Z; events at 12:00 land in hi=22.
    # Existing pseudo-median (hlats[len//2]) of [1.0, 2.0] is 2.0, matching pool-level p50.
    assert p["hourly_p50"][22] == 2.0
    assert p["hourly_p50"][23] is None  # current hour bucket empty
