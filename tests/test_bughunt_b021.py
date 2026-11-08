import landfinder as lf


def test_room_count_not_read_as_hectares():
    assert lf.meli_acres("161 m² cubiertos | 3 habitaciones") is None
    assert lf.meli_acres("2 hab") is None


def test_total_area_still_wins_over_rooms():
    got = lf.meli_acres("161 m² cubiertos | 3 habitaciones | 1.000 m² totales")
    assert abs(got - 1000 / lf.ACRE_M2) < 1e-6
