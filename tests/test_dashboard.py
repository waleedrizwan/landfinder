"""The dashboard window's data: what the run is doing now, written to data/progress.json while it goes."""
import json, threading, time

import pytest

import landfinder as lf


@pytest.fixture
def fresh_metrics(monkeypatch, tmp_path):
    m = lf.Metrics()
    m.path = str(tmp_path / "metrics.jsonl")
    monkeypatch.setattr(lf, "METRICS", m)
    monkeypatch.setattr(lf, "LIVE", lf.Live())
    monkeypatch.setattr(lf, "GIVE_UP_AFTER", 3)
    lf.STOP.clear()
    lf.WALLS.clear()
    lf.GIVEN_UP.clear()
    yield m
    lf.STOP.clear()
    lf.WALLS.clear()
    lf.GIVEN_UP.clear()


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
    assert sites["properstar"]["failed"] == 1 and sites["properstar"]["passed"] == 0  # walled and never got through
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


def test_progress_file_is_written_atomically(fresh_metrics, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "DATA", str(tmp_path / "data"))
    lf.LIVE.jobs_total = 3
    lf.save_progress()
    d = json.loads((tmp_path / "data" / "progress.json").read_text())
    assert d["jobs"] == {"total": 3, "done": 0} and d["ended"] is False and d["stopping"] is False
    assert not (tmp_path / "data" / "progress.json.tmp").exists()


def test_stop_file_stops_the_run(fresh_metrics, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    (tmp_path / "stop").write_text("stale\n")  # from an earlier run: cleared, not obeyed
    lf.write_progress(every=0.05)
    time.sleep(0.2)
    assert not lf.STOP.is_set() and not (tmp_path / "stop").exists() and (tmp_path / "progress.json").exists()
    (tmp_path / "stop").write_text("stop\n")
    for _ in range(40):
        if lf.STOP.is_set():
            break
        time.sleep(0.05)
    assert lf.STOP.is_set() and not (tmp_path / "stop").exists()
    assert any("stop requested from the dashboard" in l for l in lf.LIVE.lines)
    assert json.loads((tmp_path / "progress.json").read_text())["stopping"] is True


def test_open_dashboard_starts_the_window_with_a_tk_python(fresh_metrics, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    monkeypatch.setattr(lf, "dashboard_python", lambda: "/some/python3.12")
    calls = []
    monkeypatch.setattr(lf.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    lf.open_dashboard()
    assert calls == [["/some/python3.12", lf.os.path.join(lf.HERE, "dashboard.py"), str(tmp_path)]]


def test_no_tk_python_means_no_window_but_the_run_goes_on(fresh_metrics, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    monkeypatch.setattr(lf, "dashboard_python", lambda: None)
    monkeypatch.setattr(lf.subprocess, "Popen", lambda cmd, **kw: pytest.fail("no window should be opened"))
    lf.open_dashboard()
    assert "no dashboard window" in lf.LIVE.lines[-1]


def test_dashboard_python_wants_tk_86(monkeypatch):
    class P:
        def __init__(self, out, rc=0):
            self.stdout, self.returncode = out, rc
    versions = {"/a": P("8.5\n"), "/b": P("", 1), "/c": P("8.6\n")}
    monkeypatch.setattr(lf.os.path, "exists", lambda p: p in versions)
    monkeypatch.setattr(lf.subprocess, "run", lambda cmd, **kw: versions[cmd[0]])
    monkeypatch.setattr(lf.sys, "executable", "/a")
    monkeypatch.setattr(lf.glob, "glob", lambda pat: ["/b", "/c"])
    assert lf.dashboard_python() == "/c"


# ---- blocks: when a site takes us for a bot

@pytest.fixture
def block_files(fresh_metrics, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    monkeypatch.setattr(lf.time, "sleep", lambda s: None)  # the retry waits
    return tmp_path


def test_note_block_goes_to_metrics_and_the_blocks_file(block_files):
    lf.note_block("properstar", "wall", "https://www.properstar.com/x", "  Azure  WAF \n challenge ", "chrome")
    e = [e for e in lf.METRICS.events if e["kind"] == "block"][0]
    assert e["source"] == "properstar" and e["how"] == "wall" and e["detail"] == "Azure WAF challenge"
    line = json.loads(open(block_files / "blocks.jsonl").read().splitlines()[0])
    assert line["run"] == lf.METRICS.run and line["url"].endswith("/x") and line["time"][:2] == "20"


def test_snapshot_lists_blocks_and_history(block_files):
    (block_files / "runs.jsonl").write_text(json.dumps(dict(run="old", start="2026-11-07T06:00:00", secs=1500, args="",
                                                            pages={"properstar": 700}, failed={}, blocks={"properstar": 3})) + "\n")
    fetch("properstar", ok=True, blocked=["access denied"])  # got past a wall after waiting
    lf.note_block("properstar", "wall", "https://www.properstar.com/y", "Access Denied")
    lf.note_block("zillow", "http 403", "https://www.zillow.com/z", "", "http")
    d = lf.LIVE.snapshot()
    assert [b["how"] for b in d["blocks"]] == ["http 403", "wall"]  # newest first
    assert d["blocks"][1]["detail"] == "Access Denied" and d["blocks"][0]["via"] == "http"
    sites = {s["source"]: s for s in d["sites"]}
    assert sites["properstar"]["blocked"] == 1 and sites["properstar"]["passed"] == 1
    assert sites["zillow"]["blocked"] == 1 and sites["zillow"]["pages"] == 0 and sites["zillow"]["via"] == "http"
    assert [h["run"] for h in d["history"]] == ["old", lf.METRICS.run]
    assert d["history"][-1]["live"] is True and d["history"][-1]["blocks"] == {"properstar": 1, "zillow": 1}
    assert d["history"][-1]["pages"] == {"properstar": 1}


def test_report_appends_the_run_summary(block_files):
    fetch("landwatch")
    lf.note_block("landwatch", "no data", "https://www.landwatch.com/x", "Pardon Our Interruption")
    lf.METRICS.report("--test")
    runs = [json.loads(l) for l in open(block_files / "runs.jsonl")]
    assert len(runs) == 1 and runs[0]["blocks"] == {"landwatch": 1} and runs[0]["pages"] == {"landwatch": 1}
    assert runs[0]["args"] == "--test" and runs[0]["run"] == lf.METRICS.run


class FakeResponse:
    def __init__(self, status, text=""):
        self.status_code, self.text, self.content, self.headers = status, text, text.encode(), {}


def test_http_refusals_are_blocks(block_files):
    plain = lf.Plain(delay=0)
    answers = iter([FakeResponse(403, "<title>Access to this page has been denied</title>"), FakeResponse(429), FakeResponse(200, "<p>ok</p>")])
    plain.s.get = lambda url, timeout=0: next(answers)
    assert plain.get("https://www.zillow.com/homes/") == "<p>ok</p>"
    kinds = [(e["how"], e["detail"]) for e in lf.METRICS.events if e["kind"] == "block"]
    assert kinds == [("http 403", "Access to this page has been denied"), ("http 429", "")]
    assert all(e["source"] == "zillow" and e["via"] == "http" for e in lf.METRICS.events if e["kind"] == "block")


def test_a_404_is_not_a_block(block_files):
    plain = lf.Plain(delay=0)
    plain.s.get = lambda url, timeout=0: FakeResponse(404)
    assert plain.get("https://www.olx.pl/x") is None
    assert not [e for e in lf.METRICS.events if e["kind"] == "block"]


def test_results_page_without_listings_is_a_block(block_files):
    class Fetch(lf.Plain):
        def get(self, url, what="search"):
            lf.METRICS.add("fetch", via="http", source="zillow", what=what, url=url, ok=True, attempts=1, statuses=[200], secs=1, bytes=1)
            return "<html><title>Please verify you are a human</title><body>captcha</body></html>"

    class Args:
        pages, listings, house_listings = 3, 250, 60

    class Store:
        done, searches, stopped = set(), {}, []
        believable = lambda self, sid: 0
        stop = lambda self, sid, why: self.stopped.append((sid, why))
    store = Store()
    stopped = store.stopped
    new = lf.crawl_search(Fetch(delay=0), store, "zillow", "arizona", Args(), lf.zillow_url, lambda html, key, kind: None)
    assert new == 0 and stopped == [("zillow arizona", "fetch failed")]
    e = [e for e in lf.METRICS.events if e["kind"] == "block"]
    assert len(e) == 1 and e[0]["how"] == "no data" and e[0]["detail"] == "Please verify you are a human" and e[0]["via"] == "http"


def test_chrome_bot_check_page_is_a_wall(block_files):
    """A Browser whose page keeps answering with a bot-check title: three attempts, three wall events, no HTML."""
    class Page:
        url = ""
        def goto(self, url, **kw): pass
        def wait_for_timeout(self, ms): pass
        def wait_for_load_state(self, *a, **kw): pass
        def title(self): return "Access Denied"
        def content(self): return "<html></html>"

    br = lf.Browser.__new__(lf.Browser)
    br.page, br.net, br.pages, br.busy, br.delay, br.label, br.slot, br.doc_status = Page(), dict(bytes=0, requests=0, types=lf.Counter()), 0, 0.0, 0, "", None, None
    assert br.get("https://www.properstar.com/portugal/buy/land-plot") is None
    blocks = [e for e in lf.METRICS.events if e["kind"] == "block"]
    assert len(blocks) == 3 and {b["how"] for b in blocks} == {"wall"} and blocks[0]["detail"] == "access denied"
    f = [e for e in lf.METRICS.events if e["kind"] == "fetch"][0]
    assert f["ok"] is False and f["blocked"] == ["access denied"] * 3 and f["attempts"] == 3
    assert len(open(block_files / "blocks.jsonl").read().splitlines()) == 3


# ---- giving up on a site that keeps blocking

def wall(source="properstar", n=1):
    for _ in range(n):
        lf.note_block(source, "wall", "https://www.%s.com/p" % source, "Access Denied")


def test_three_blocks_in_a_row_give_the_site_up(block_files):
    wall(n=2)
    assert not lf.GIVEN_UP
    wall()
    assert lf.GIVEN_UP == {"properstar"} and lf.given_up("https://www.properstar.com/portugal/buy/land-plot")
    assert not lf.given_up("https://www.landwatch.com/x") and not lf.given_up("landwatch")
    assert "properstar has blocked 3 pages in a row" in lf.LIVE.lines[-1]
    assert [e for e in lf.METRICS.events if e["kind"] == "gave_up"][0]["source"] == "properstar"
    d = lf.LIVE.snapshot()
    assert d["given_up"] == ["properstar"] and d["history"][-1]["gave_up"] == ["properstar"]
    wall()  # a fourth block doesn't log or record giving up twice
    assert len([e for e in lf.METRICS.events if e["kind"] == "gave_up"]) == 1


def test_a_page_that_parsed_resets_the_streak(block_files):
    wall(n=2)
    lf.page_ok("properstar")
    wall(n=2)
    assert not lf.GIVEN_UP
    wall()
    assert lf.GIVEN_UP == {"properstar"}


def test_give_up_zero_keeps_trying(block_files, monkeypatch):
    monkeypatch.setattr(lf, "GIVE_UP_AFTER", 0)
    wall(n=10)
    assert not lf.GIVEN_UP


def test_fetchers_skip_a_site_that_was_given_up(block_files):
    wall(n=3)
    plain = lf.Plain(delay=0)
    calls = []
    plain.s.get = lambda url, timeout=0: calls.append(url) or FakeResponse(200, "<p>ok</p>")
    assert plain.get("https://www.properstar.com/x") is None and calls == []  # no request sent
    assert plain.get("https://www.olx.pl/x") == "<p>ok</p>" and calls == ["https://www.olx.pl/x"]
    br = lf.Browser.__new__(lf.Browser)
    br.page = None  # would blow up if the page were used
    assert br.get("https://www.properstar.com/portugal/buy/land-plot") is None


def test_streak_counts_consecutive_blocks_across_different_pages_only(block_files):
    """Blocks on two sites don't add up: each site has its own streak."""
    wall("properstar", 2)
    wall("landwatch", 2)
    assert not lf.GIVEN_UP
    wall("landwatch")
    assert lf.GIVEN_UP == {"landwatch"}


def test_step_after_the_searches_reaches_the_dashboard(fresh_metrics):
    """Once the searches are done the window shows what's running now (translating, writing), with how far it got."""
    assert lf.LIVE.snapshot()["step"] is None
    lf.LIVE.set_step("translating 10 titles to English with Claude", 3, 8)
    step = lf.LIVE.snapshot()["step"]
    assert step["text"].startswith("translating") and (step["done"], step["total"]) == (3, 8) and step["since_s"] >= 0
    lf.LIVE.set_step("writing results.csv, report.html and the site")
    assert lf.LIVE.snapshot()["step"]["total"] is None
    lf.LIVE.set_step(None)
    assert lf.LIVE.snapshot()["step"] is None


def test_translate_counts_its_batches(fresh_metrics, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    monkeypatch.setattr(lf.shutil, "which", lambda name: "/bin/claude")
    seen = []

    def fake_run(cmd, input, **kw):
        batch = json.loads(input)
        seen.append(dict(lf.LIVE.step))
        return type("P", (), dict(stdout=json.dumps(["EN " + t for t in batch]), stderr=""))()
    monkeypatch.setattr(lf.subprocess, "run", fake_run)
    out = lf.translate(["título %d" % i for i in range(170)])  # 80 a batch: 3 batches
    assert out["título 5"] == "EN título 5"
    step = lf.LIVE.step
    assert (step["done"], step["total"]) == (3, 3) and "170 titles" in step["text"]
    assert seen[0]["total"] == 3
