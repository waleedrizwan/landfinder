import pytest

import landfinder as lf

M2_PER_ACRE = 4046.8564224


@pytest.mark.parametrize("text,m2", [
    ("Terreno de 1.250 m²", 1250),
    ("Terreno 12.500 m2 à vendre", 12500),
    ("parcela 20.000 m2 en venta", 20000),
    ("Terrain 1.250,00 m²", 1250),
])
def test_dot_thousands_separator_in_m2(text, m2):
    got = lf.acres_from_text(text)
    assert got == pytest.approx(m2 / M2_PER_ACRE, rel=1e-3)
