"""b039: a stale record must not shadow a fresh repost of the same parcel in the de-duplication."""
import json
import landfinder as lf
from conftest import FakeStore


def _rec(url, scraped_at):
    return {"source": "landwatch", "country": "US", "region": "NM", "title": "Lot (PID 123) Old Port Road",
            "description": "", "price": 50000, "currency": "USD", "acres": 5.0, "url": url,
            "scraped_at": scraped_at}


def test_fresh_repost_survives_dedup_against_stale_record(workdir):
    old = _rec("https://www.landwatch.com/old", "2026-11-01T10:00:00")
    new = _rec("https://www.landwatch.com/new", "2026-11-08T10:00:00")
    other = dict(_rec("https://www.landwatch.com/other", "2026-11-08T10:00:00"), title="Another parcel", price=90000)
    lf.build_outputs(FakeStore([old, new, other]), english=False)
    data = json.loads((workdir / "site" / "data.json").read_text())
    urls = [row[8] for row in data["rows"]]
    assert "https://www.landwatch.com/new" in urls, "fresh repost was dropped; site shows %r" % urls
