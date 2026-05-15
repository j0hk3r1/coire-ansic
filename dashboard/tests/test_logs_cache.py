import app


def test_logs_cache_dedupes(monkeypatch):
    calls = []

    def fake_bifrost_get(path, **kw):
        calls.append((path, kw))
        return {"logs": []}

    monkeypatch.setattr(app, "bifrost_get", fake_bifrost_get)

    cache = app._LogsCache()
    a = cache.fetch(window_hours=24, limit=1000)
    b = cache.fetch(window_hours=24, limit=1000)

    assert a is b
    assert len(calls) == 1


def test_logs_cache_separates_by_window(monkeypatch):
    monkeypatch.setattr(app, "bifrost_get", lambda *a, **k: {"logs": []})
    cache = app._LogsCache()
    cache.fetch(window_hours=24, limit=1000)
    cache.fetch(window_hours=1, limit=1000)
    assert len(cache._memo) == 2
