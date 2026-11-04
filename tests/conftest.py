import os, shutil, sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import landfinder as lf  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")

# Fixed exchange rates (units per USD, like open.er-api.com) so tests never touch the network.
RATES = {"USD": 1.0, "CAD": 1.4, "EUR": 0.9, "PYG": 7500.0, "PLN": 4.0, "JPY": 150.0}


def fixture_html(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def rates():
    return dict(RATES)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A scratch copy of the project layout: templates in, results.csv / report.html / site/ out."""
    for name in ("report_template.html", "site_template.html"):
        shutil.copy(os.path.join(ROOT, name), tmp_path / name)
    monkeypatch.setattr(lf, "HERE", str(tmp_path))
    monkeypatch.setattr(lf, "SITE", str(tmp_path / "site"))
    monkeypatch.setattr(lf, "DATA", str(tmp_path / "data"))
    monkeypatch.setattr(lf, "load_fx", lambda: dict(RATES))
    monkeypatch.setattr(lf, "log", lambda msg: None)
    return tmp_path


class FakeStore:
    """Stands in for lf.Store: just hands back the records it was given."""

    def __init__(self, recs):
        self.recs = recs

    def all(self):
        return [dict(r) for r in self.recs]
