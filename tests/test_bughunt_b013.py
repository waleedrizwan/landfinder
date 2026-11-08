import pytest
import landfinder as lf


def test_building_area_before_land_area_not_used_as_lot():
    acres = lf.jp_acres("建物面積 80㎡ 土地面積 200㎡")
    assert acres == pytest.approx(200 / lf.ACRE_M2, rel=1e-3)


def test_building_area_only_gives_no_lot():
    assert lf.jp_acres("建物面積 80㎡ 築30年") is None
    assert lf.jp_acres("延床面積 95㎡") is None
