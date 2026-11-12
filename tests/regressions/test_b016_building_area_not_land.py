import landfinder as lf


def test_building_area_before_land_area_is_not_used_as_land():
    acres = lf.jp_acres("建物面積 80㎡ 土地面積 500㎡")
    assert acres is not None
    assert abs(acres - 500 / lf.ACRE_M2) < 1e-6


def test_building_only_area_is_not_land_area():
    assert lf.jp_acres("延床面積 80㎡ 築30年") is None
