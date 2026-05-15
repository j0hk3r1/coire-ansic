import app
from freezegun import freeze_time


@freeze_time("2026-05-08T13:00:00Z")
def test_heatmap_aggregates_by_day_hour_pool():
    logs = {
        "logs": [
            {"timestamp": "2026-05-08T12:15:00.000Z", "routing_rule_name": "best"},
            {"timestamp": "2026-05-08T12:30:00.000Z", "routing_rule_name": "best"},
            {"timestamp": "2026-05-07T05:00:00.000Z", "routing_rule_name": "code"},
            {"timestamp": "2026-04-25T00:00:00.000Z", "routing_rule_name": "best"},  # outside 7d
        ]
    }

    class C:
        def fetch(self, **kw):
            return logs

    out = app.load_activity_heatmap(days=7, cache=C())
    assert "pools" in out
    assert "best" in out["pools"]
    assert out["pools"]["best"][0][12] == 2  # day-offset 0 (today), hour 12
    assert out["pools"]["code"][1][5] == 1   # day-offset 1, hour 5
