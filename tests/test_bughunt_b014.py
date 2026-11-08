import pytest
from conftest import lf


def test_bedrooms_not_hectares():
    assert lf.meli_acres("2 habitaciones") is None


def test_real_lot_found_after_bedrooms():
    got = lf.meli_acres("3 hab | 500 m² totales")
    assert got == pytest.approx(500 / lf.ACRE_M2)
