import landfinder as lf
from conftest import FakeStore


def test_script_close_in_title_does_not_end_data_script(workdir):
    evil = "</script><script>alert(1)</script>"
    rec = {"source": "kijiji", "country": "CA", "region": "Halifax", "title": evil,
           "description": "", "price": 50000, "currency": "CAD", "acres": 2.0,
           "url": "https://www.kijiji.ca/v-land-for-sale/evil", "scraped_at": "2099-01-01T00:00:00"}
    lf.build_outputs(FakeStore([rec]), english=False)
    html = (workdir / "report.html").read_text()
    start = html.index("const D=")
    end = html.index("</script>", start)  # where the HTML parser ends the data script
    embedded = html[start:end]
    assert "alert(1)" in embedded, "scraped </script> ended the data script block early"
    assert "</script><script>alert" not in html, "scraped text is live markup in report.html"
