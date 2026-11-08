import json
import pytest
from conftest import lf


def _page(attrs_texts):
    res = {"polycard": {
        "metadata": {"url": "articulo.mercadolibre.com.ar/MLA-1"},
        "components": [
            {"type": "price", "price": {"current_price": {"value": 100000, "currency": "USD"}}},
            {"type": "title", "title": {"text": "Casa"}},
            {"type": "attributes_list", "attributes_list": {"texts": attrs_texts}},
        ]}}
    st = {"results": [res], "pagination": {"page_count": 1}}
    return "_n.ctx.r=" + json.dumps({"appProps": {"pageProps": {"initialState": st}}})


def test_bedroom_count_not_read_as_hectares():
    out, _, _ = lf.parse_meli(_page(["161 m² cubiertos", "3 habitaciones"]), "ar", kind="houses")
    assert out[0]["acres"] is None


def test_abbrev_bedrooms_not_hectares():
    assert lf.meli_acres("3 hab. | 2 baños") is None


def test_real_lot_after_bedrooms_is_reached():
    got = lf.meli_acres("3 habitaciones | 500 m² totales")
    assert got == pytest.approx(500 / lf.ACRE_M2)
