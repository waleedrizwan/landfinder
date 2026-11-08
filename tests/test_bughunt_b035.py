import os

import landfinder as lf
from conftest import FakeStore


def test_report_html_data_cannot_close_script_block(workdir):
    evil = "</script><img src=x onerror=alert(1)>"
    rec = {"source": "inmuebles24", "country": "MX", "region": "Tulum", "title": evil,
           "price": 100000, "currency": "MXN", "acres": 0.25, "url": "https://example.com/a",
           "scraped_at": "2026-10-01T00:00:00"}
    lf.build_outputs(FakeStore([rec]), english=False)
    html = open(os.path.join(str(workdir), "report.html"), encoding="utf-8").read()
    tpl = open(os.path.join(str(workdir), "report_template.html"), encoding="utf-8").read()
    assert "<img src=x onerror" not in html
    assert html.lower().count("</script>") == tpl.lower().count("</script>")
