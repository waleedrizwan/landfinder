import pytest

import landfinder as lf


@pytest.mark.parametrize("text, m2", [
    ("Terreno 1.250 m²", 1250),
    ("12.500 m2", 12500),
])
def test_dot_thousands_m2_is_not_read_as_decimal(text, m2):
    # '1.250 m²' is 1,250 m² (about 0.309 acres), not 1.25 m² (0.0003 acres)
    assert lf.acres_from_text(text) == pytest.approx(m2 / lf.ACRE_M2)
