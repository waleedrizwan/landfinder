"""The live dashboard: what the run is doing now, served on localhost while it goes."""
import json, threading, time, urllib.request

import pytest

import landfinder as lf


@pytest.fixture
def fresh_metrics(monkeypatch, tmp_path):
    m = lf.Metrics()
    m.path = str(tmp_path / "metrics.jsonl")
    monkeypatch.setattr(lf, "METRICS", m)
    monkeypatch.setattr(lf, "LIVE", lf.Live())
    lf.STOP.clear()
    yield m
    lf.STOP.clear()


def fetch(source, ok=True, what="search", blocked=(), secs=3.0, via="chrome"):
    lf.METRICS.add("fetch", via=via, source=source, what=what, url="https://www.%s.com/x" % source, ok=ok,
                   attempts=1, blocked=list(blocked), errors=0, status=200, ready_hit=None, secs=secs, html_kb=10,
                   bytes=10000, requests=1, types={}, nav_s=1, ready_s=0, read_s=0, delay_s=2, wait_s=0)


def test_snapshot_counts_pages_jobs_and_listings(fresh_metrics):
    lf.LIVE.jobs_total = 10
    with lf.METRICS.phase("chrome pool"):
        lf.LIVE.phases["chrome pool"] = time.time() - 100  # 100 s into the pool
        for _ in range(4):
            fetch("landwatch")
        fetch("properstar", ok=False, blocked=["access denied"])
        fetch("kijiji", what="verify", via="http")
        lf.METRICS.add("parsed", source="landwatch", search="landwatch arizona", n=25, new=20)
        lf.METRICS.add("parsed", source="landwatch", search="landwatch maine", n=25, new=5)
        for _ in range(4):
            lf.METRICS.add("job", source="landwatch", key="x", section="land", secs=20, pages=1, new=5)
        lf.LIVE.now("https://www.landwatch.com/arizona-land-for-sale", "arizona land  page 2  (landwatch)")
        d = lf.LIVE.snapshot()
    assert d["phase"] == "chrome pool" and d["jobs"] == {"total": 10, "done": 4}
    assert 140 <= d["eta_s"] <= 160  # 100 s for 4 jobs -> 25 s each, 6 left
    sites = {s["source"]: s for s in d["sites"]}
    assert sites["landwatch"]["pages"] == 4 and sites["landwatch"]["recent"] == 4 and sites["landwatch"]["new"] == 25
    assert sites["properstar"]["failed"] == 1 and sites["properstar"]["blocked"] == 1
    assert sites["kijiji"]["verify"] == 1 and sites["kijiji"]["via"] == "http"
    assert d["listings_new"] == 25 and d["stopping"] is False
    w = d["workers"][0]
    assert w["label"].startswith("arizona land") and w["busy"] and w["pages"] == 6  # all fetches ran in this thread
    assert lf.LIVE.phase is None  # the phase ended


def test_phase_is_the_main_threads(fresh_metrics):
    """The HTTP sites' phases run in their own threads and must not hide (or end) the main phase."""
    with lf.METRICS.phase("chrome pool"):
        def side():
            with lf.METRICS.phase("http kijiji"):
                pass
        t = threading.Thread(target=side)
        t.start()
        t.join()
        assert lf.LIVE.phase == "chrome pool"


def test_log_lines_reach_the_dashboard(fresh_metrics):
    lf.log("hello dashboard")
    assert lf.LIVE.lines[-1].endswith("hello dashboard")
    assert lf.LIVE.snapshot()["log"][-1].endswith("hello dashboard")


def test_idle_workers_are_marked(fresh_metrics):
    lf.LIVE.now("https://www.olx.pl/x")
    lf.LIVE.idle()
    w = lf.LIVE.snapshot()["workers"][0]
    assert w["label"] == "finished" and not w["busy"]


def test_server_serves_page_progress_and_stop(fresh_metrics):
    srv = lf.serve_dashboard(0)  # any free port
    try:
        port = srv.server_address[1]
        page = urllib.request.urlopen("http://127.0.0.1:%d/" % port).read().decode()
        assert "<title>Land Finder" in page and "/progress.json" in page
        d = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/progress.json" % port).read())
        assert d["jobs"] == {"total": 0, "done": 0} and d["stopping"] is False
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen("http://127.0.0.1:%d/nothing" % port)
        assert e.value.code == 404
        r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/stop" % port, method="POST"))
        assert r.status == 204 and lf.STOP.is_set()
        assert json.loads(urllib.request.urlopen("http://127.0.0.1:%d/progress.json" % port).read())["stopping"] is True
    finally:
        srv.shutdown()
        srv.server_close()


def test_busy_port_is_reported_not_fatal(fresh_metrics):
    a = lf.serve_dashboard(0)
    try:
        assert lf.serve_dashboard(a.server_address[1]) is None
        assert "dashboard not started" in lf.LIVE.lines[-1]
    finally:
        a.shutdown()
        a.server_close()
