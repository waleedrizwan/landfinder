import landfinder as lf
from conftest import FakeStore


def test_script_end_tag_in_title_cannot_break_report(workdir):
    evil = "</script><img src=x onerror=alert(1)>"
    rec = {"source": "kijiji", "country": "CA", "region": "Ontario", "title": evil,
           "description": "", "price": 5000, "currency": "CAD", "acres": 2.0,
           "url": "https://www.kijiji.ca/v-land/1", "scraped_at": "2026-10-01T00:00:00"}
    lf.build_outputs(FakeStore([rec]), english=False)
    html = (workdir / "report.html").read_text()
    start = html.index("const D=")
    end = html.index("</script>", start)
    # the first </script> after the data must be the template's own (after the whole data array),
    # not the one planted in the title
    assert "onerror" in html[start:end], "title's </script> ends the data script block early"
