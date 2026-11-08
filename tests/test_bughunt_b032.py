import json

import landfinder as lf
from conftest import FakeStore


def _rec(url, scraped_at):
    return {"source": "kijiji", "country": "CA", "region": "Ontario", "title": "Nice lot near lake",
            "description": "", "price": 5000, "currency": "CAD", "acres": 2.0,
            "url": url, "scraped_at": scraped_at}


def test_fresh_repost_survives_dedupe_against_stale_record(workdir):
    # old URL first in store order (first-seen), scraped >3 days before the newest scrape
    recs = [_rec("https://www.kijiji.ca/v/old", "2026-10-01T00:00:00"),
            _rec("https://www.kijiji.ca/v/new", "2026-10-10T00:00:00")]
    other = _rec("https://www.kijiji.ca/v/other", "2026-10-10T00:00:00")  # keeps the site cutoff at the new run
    other.update(title="Different parcel", price=9000)
    recs.append(other)
    lf.build_outputs(FakeStore(recs), english=False)
    with open(workdir / "site" / "data.json") as f:
        urls = {row[8] for row in json.load(f)["rows"]}
    assert "https://www.kijiji.ca/v/new" in urls
