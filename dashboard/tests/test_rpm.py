from datetime import datetime, timezone
import app
from freezegun import freeze_time


@freeze_time("2026-05-08T13:00:00Z")
def test_rpm_buckets_60_minutes(monkeypatch):
    logs = {
        "logs": [
            {"timestamp": "2026-05-08T12:30:15.000Z"},
            {"timestamp": "2026-05-08T12:30:45.000Z"},
            {"timestamp": "2026-05-08T12:59:00.000Z"},
            {"timestamp": "2026-05-08T11:00:00.000Z"},  # outside window
        ]
    }

    class C:
        def fetch(self, **kw):
            return logs

    out = app.requests_per_minute_60min(cache=C())
    assert len(out) == 60
    assert sum(out) == 3
    assert out[30] == 2  # 12:30 bucket
    assert out[59] == 1  # 12:59 bucket
