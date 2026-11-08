import json
import types

import landfinder as lf


class DeadBrowser:
    """A LandWatch ad page that was removed: nothing usable comes back."""
    def __init__(self, *a, **k):
        pass

    def get(self, url, **k):
        return None

    def close(self):
        pass


def test_dead_landwatch_ad_keeps_old_scraped_at(workdir, monkeypatch):
    monkeypatch.setattr(lf, "Browser", DeadBrowser)
    monkeypatch.setattr(lf, "Plain", lambda **k: None)
    store = lf.Store()
    url = "https://www.landwatch.com/acres-for-sale/pid/1"
    old = "2026-10-01T00:00:00"
    rec = {"source": "landwatch", "country": "US", "region": "Texas", "title": "10 acres",
           "description": "Nice lot", "price": 5000, "currency": "USD", "acres": 10.0,
           "lat": 30.0, "lon": -97.0, "url": url}
    # a record from an earlier run
    with open(store.path, "w") as f:
        f.write(json.dumps(dict(rec, scraped_at=old)) + "\n")
    a = types.SimpleNamespace(sources="landwatch", delay=0, workers=1, headless=True, load_all=False)
    lf.verify_cheapest(store, a, n=60, rounds=1)
    latest = [r for r in store.all() if r["url"] == url][0]
    assert latest["scraped_at"] == old
