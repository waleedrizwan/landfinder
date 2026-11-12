import landfinder as lf
from conftest import FakeStore


def test_report_data_cannot_close_script_block(workdir):
    evil = "</script><img src=x onerror=alert(1)>"
    rec = {"source": "kijiji", "country": "CA", "region": "Ontario", "title": evil,
           "description": "", "price": 5000, "currency": "CAD", "acres": 2.0,
           "url": "https://www.kijiji.ca/v-land/1", "scraped_at": "2026-10-01T00:00:00"}
    lf.build_outputs(FakeStore([rec]), english=False)
    html = (workdir / "report.html").read_text()
    assert "</script><img" not in html, "scraped title closes the inline <script> data block"
