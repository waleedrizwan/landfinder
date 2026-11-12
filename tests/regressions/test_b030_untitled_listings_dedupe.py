import csv

import landfinder as lf
from conftest import FakeStore


def _rec(n, title):
    return {"source": "landwatch", "country": "US", "region": "Texas", "title": title,
            "description": "", "price": 5000, "currency": "USD", "acres": 2.0,
            "url": "https://www.landwatch.com/lot/%d" % n, "scraped_at": "2026-10-01T00:00:00"}


def test_untitled_distinct_listings_are_not_deduped(workdir):
    recs = [_rec(1, "•"), _rec(2, "-"), _rec(3, "")]
    lf.build_outputs(FakeStore(recs), english=False)
    with open(workdir / "results.csv", newline="") as f:
        urls = {r["url"] for r in csv.DictReader(f)}
    assert urls == {r["url"] for r in recs}
