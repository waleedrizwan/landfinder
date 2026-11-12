import landfinder as lf


def test_meli_decimal_point_hectares():
    # Mexican listings use a decimal point: "1.5 ha" is 1.5 hectares
    got = lf.meli_acres("1.5 ha totales")
    assert got is not None and abs(got - 1.5 * 2.4710538) < 0.01


def test_meli_small_fraction_of_hectare():
    got = lf.meli_acres("0.25 ha totales")
    assert got is not None and abs(got - 0.25 * 2.4710538) < 0.01
