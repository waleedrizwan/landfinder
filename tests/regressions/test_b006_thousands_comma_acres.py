import pytest
import landfinder as lf


@pytest.mark.parametrize("text,acres", [
    ("Rolling terrain, 1,500 acres ranch", 1500),
    ("Ranch near La Junta, CO - 2,500 acres", 2500),
    ("1,250 acres terrain", 1250),
])
def test_thousands_comma_acres_with_romance_word(text, acres):
    assert lf.acres_from_text(text) == pytest.approx(acres)
