import pytest

import landfinder as lf


@pytest.mark.parametrize("text, m2", [
    ("Terreno 1.500 m2 en venta", 1500),
    ("10.000 m2", 10000),
    ("1.250 m²", 1250),
])
def test_dot_thousands_separator_in_square_metres(text, m2):
    assert lf.acres_from_text(text) == pytest.approx(m2 / lf.ACRE_M2)
