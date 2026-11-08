import pytest

import landfinder as lf


def test_jp_acres_building_area_before_land_area():
    # the land area (200 m2), not the building's floor area (80 m2)
    acres = lf.jp_acres("建物面積：80㎡ 土地面積：200㎡")
    assert acres == pytest.approx(200 / lf.ACRE_M2, rel=1e-3)
