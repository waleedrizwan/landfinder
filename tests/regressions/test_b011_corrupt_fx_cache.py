import landfinder as lf


class _Resp:
    def json(self):
        return {"rates": {"USD": 1.0, "EUR": 0.9}}


def test_corrupt_fx_cache_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    # simulate a run killed mid json.dump: truncated JSON left behind
    (tmp_path / "fx.json").write_text('{"date": "2020-01-01", "rates": {"USD": 1.0, "EU')
    monkeypatch.setattr(lf.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(lf, "log", lambda msg: None)
    assert lf.load_fx()["EUR"] == 0.9
