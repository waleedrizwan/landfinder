import landfinder as lf


def test_room_count_after_built_area_not_read_as_hectares():
    # Only built area + room count: no land area is given, so acres must be None.
    assert lf.meli_acres("161 m² cubiertos | 3 habitaciones") is None
    assert lf.meli_acres("161 m² cubiertos | 2 hab | 4 hall") is None
