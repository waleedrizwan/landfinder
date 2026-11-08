import json
import types

import landfinder as lf


class DeadBrowser:
    """Every fetch is blocked / fails: Browser.get returns None."""
    def __init__(self, *a, **k):
        pass

    def get(self, url, **k):
        return None

    def close(self):
        pass


def test_failed_fetch_is_not_marked_verified(workdir, monkeypatch):
    monkeypatch.setattr(lf, "Browser", DeadBrowser)
    monkeypatch.setattr(lf, "Plain", lambda **k: None)
    store = lf.Store()
    url = "https://www.landwatch.com/acres-for-sale/pid/2"
    rec = {"source": "landwatch", "country": "US", "region": "Texas", "title": "10 acres",
           "description": "Only $2,700 cash ... ", "price": 2700, "currency": "USD", "acres": 10.0,
           "lat": 30.0, "lon": -97.0, "url": url, "scraped_at": "2026-10-01T00:00:00"}
    with open(store.path, "w") as f:
        f.write(json.dumps(rec) + "\n")
    a = types.SimpleNamespace(sources="landwatch", delay=0, workers=1, headless=True, load_all=False)
    lf.verify_cheapest(store, a, n=60, rounds=1)
    latest = [r for r in store.all() if r["url"] == url][-1]
    assert not latest.get("verified_at")
