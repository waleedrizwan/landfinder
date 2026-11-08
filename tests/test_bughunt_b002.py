import pytest
import landfinder as lf


@pytest.mark.parametrize("text,acres", [
    ("Terre à vendre, 1,000 acres", 1000),
    ("Land 2,500 acres near Baton Rouge, LA", 2500),
])
def test_comma_grouped_acres_with_romance_stopword(text, acres):
    assert lf.acres_from_text(text) == pytest.approx(acres)
