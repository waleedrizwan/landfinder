import csv
import os

import landfinder as lf
from conftest import FakeStore


def test_distinct_listings_with_generic_title_are_not_merged(workdir):
    recs = [
        {"source": "inmuebles24", "country": "MX", "region": region, "title": "Terreno en venta",
         "price": 100000, "currency": "MXN", "acres": 0.25, "url": "https://example.com/" + region,
         "scraped_at": "2026-10-01T00:00:00"}
        for region in ("Tulum", "Oaxaca")
    ]
    lf.build_outputs(FakeStore(recs), english=False)
    with open(os.path.join(str(workdir), "results.csv"), newline="") as f:
        urls = {row["url"] for row in csv.DictReader(f)}
    assert urls == {r["url"] for r in recs}
