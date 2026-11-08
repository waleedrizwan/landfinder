"""b038: a scraped title containing </script> must not break out of the report's data <script>."""
import landfinder as lf
from conftest import FakeStore


def test_report_data_script_cannot_be_closed_by_title(workdir):
    rec = {"source": "landwatch", "country": "US", "region": "NM",
           "title": "Lot </script><script>alert(document.cookie)</script>",
           "description": "", "price": 50000, "currency": "USD", "acres": 5.0,
           "url": "https://www.landwatch.com/x"}
    lf.build_outputs(FakeStore([rec]), english=False)
    html = (workdir / "report.html").read_text()
    start = html.index("const D=")
    first_close = html.index("</script>", start)
    # the template's own code (`let key=...`) follows the data; the first </script> must come after it
    assert html.index("let key=", start) < first_close, "title's </script> terminates the data script block"
