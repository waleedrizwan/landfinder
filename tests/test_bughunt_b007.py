import pytest

import landfinder as lf


@pytest.mark.parametrize("text, m2", [
    ("12.000 m2 terreno", 12000),
    ("Terreno de 1.250 m² com água", 1250),
    ("Terreno edificabile 3.500 m2", 3500),
])
def test_dot_thousands_m2_in_romance_text(text, m2):
    assert lf.acres_from_text(text) == pytest.approx(m2 / lf.ACRE_M2)


def test_kijiji_description_with_dot_thousands_m2():
    ad = dict(title="Terreno", description="Terreno de 12.000 m2 en venta", attributes={})
    assert lf.kijiji_acres(ad) == pytest.approx(12000 / lf.ACRE_M2)
