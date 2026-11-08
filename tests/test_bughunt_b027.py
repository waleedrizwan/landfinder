import json

import landfinder as lf


def page(list_results):
    data = {"props": {"pageProps": {"searchPageState": {"cat1": {"searchResults": {"listResults": list_results}}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">%s</script>' % json.dumps(data)


def test_parse_zillow_null_list_results_does_not_raise(rates, workdir):
    # odd page: must be treated like other malformed JSON (None = not a results page, or [])
    out = lf.parse_zillow(page(None), "ohio")
    assert out is None or out == []
