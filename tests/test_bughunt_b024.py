import landfinder as lf


def test_bedrooms_before_lot_not_read_as_hectares():
    assert lf.meli_acres("3 habitaciones | 2 baños") is None
