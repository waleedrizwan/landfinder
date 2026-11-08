#!/usr/bin/env python3
"""
Bug-hunt loop for landfinder: hunt -> group -> verify -> fix -> gate, with a ledger so rounds don't repeat.

Each stage is a separate, fresh `claude -p` session, so the agent that finds a bug never grades it.

  python3 bughunt/bughunt.py                      # one round over every chunk x category, then verify + fix
  python3 bughunt/bughunt.py --rounds 3           # keep going; stops early after a round with nothing confirmed
  python3 bughunt/bughunt.py --chunks core,outputs --categories parsing,security
  python3 bughunt/bughunt.py --hunt-only          # just collect findings into the ledger
  python3 bughunt/bughunt.py --no-fix             # hunt + verify, leave fixing to you

Run it on a branch. Every fix is its own commit; nothing is pushed or merged.
"""
import argparse, hashlib, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEDGER = os.path.join(HERE, "findings.json")
PROMPTS = os.path.join(HERE, "prompts")

READ_TOOLS = "Read,Grep,Glob,Bash(python3:*),Bash(grep:*)"
VERIFY_TOOLS = READ_TOOLS + ",Write,Bash(rm tests/test_bughunt_*)"
FIX_TOOLS = READ_TOOLS + ",Edit"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def prompt(name, **fields):
    with open(os.path.join(PROMPTS, name + ".md")) as f:
        text = f.read()
    for k, v in fields.items():
        text = text.replace("{" + k + "}", v)
    return text


def claude(text, tools, timeout=1800):
    """Run one fresh, non-interactive Claude session and return the JSON its answer contains."""
    r = subprocess.run(["claude", "-p", text, "--output-format", "json", "--allowedTools", tools],
                       cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    try:
        answer = json.loads(r.stdout).get("result", "")
    except json.JSONDecodeError:
        answer = r.stdout
    m = re.search(r"(\[.*\]|\{.*\})", answer, re.S)  # the answer may be wrapped in prose or a code fence
    if not m:
        raise ValueError(f"no JSON in answer: {answer[:300]!r} {r.stderr[:300]!r}")
    return json.loads(m.group(1))


def load_ledger():
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            return json.load(f)
    return {"findings": []}


def save_ledger(ledger):
    with open(LEDGER, "w") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)
        f.write("\n")


def fingerprint(f):
    """Same function + same trigger = same bug, whichever pass found it."""
    trigger = re.sub(r"\W+", " ", f.get("trigger", "").lower())[:80]
    key = f"{f.get('file')}|{f.get('function')}|{trigger}"
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def root_causes(ledger):
    """Root causes already in the ledger, one line each, with what became of them."""
    seen = {}
    for f in ledger["findings"]:
        if f.get("root_cause") and f["root_cause"] not in seen:
            seen[f["root_cause"]] = f["status"]
    return seen


def known_summary(ledger):
    lines = [f"- {rc} ({st})" for rc, st in root_causes(ledger).items()]
    lines += [f"- {f['function']}: {f['trigger'][:120]} (not a bug: {f.get('verify_reason', '')[:100]})"
              for f in ledger["findings"] if f["status"] in ("not_a_bug", "not_reproducible")]
    if os.path.exists(os.path.join(ROOT, "QA_REPORT.md")):
        lines.append("- everything in QA_REPORT.md (read it)")
    return "\n".join(lines) or "- nothing yet"


def hunt(ledger, chunks, categories, workers):
    cfg = json.load(open(os.path.join(HERE, "chunks.json")))
    known = known_summary(ledger)
    jobs = [(c, cat) for c in chunks for cat in categories]
    log(f"hunting: {len(jobs)} passes ({len(chunks)} chunks x {len(categories)} categories)")

    def one(job):
        c, cat = job
        try:
            return c, cat, claude(prompt("hunt", SCOPE=cfg["chunks"][c],
                                         CATEGORIES=f"{cat}: {cfg['categories'][cat]}", KNOWN=known), READ_TOOLS)
        except Exception as e:
            log(f"  {c}/{cat} failed: {e}")
            return c, cat, []

    seen = {f["fp"] for f in ledger["findings"]}
    new = 0
    with ThreadPoolExecutor(workers) as ex:
        for c, cat, found in ex.map(one, jobs):
            for f in found if isinstance(found, list) else []:
                f["fp"] = fingerprint(f)
                if f["fp"] in seen or f.get("confidence") == "low":
                    continue
                seen.add(f["fp"])
                f.update(id=f"B{len(ledger['findings']) + 1:03d}", chunk=c, status="found",
                         found_at=datetime.now().isoformat(timespec="seconds"))
                ledger["findings"].append(f)
                new += 1
            log(f"  {c}/{cat}: {len(found) if isinstance(found, list) else 0} reported")
    save_ledger(ledger)
    log(f"{new} new findings")
    return new


def finding_text(f):
    keep = {k: f[k] for k in ("id", "file", "line", "function", "trigger", "expected", "actual", "impact") if k in f}
    return json.dumps(keep, indent=2, ensure_ascii=False)


def group(ledger):
    """Parallel hunters report one defect many times (round 1: 38 confirmed findings, 10 root causes).
    One session clusters the new findings; only one per root cause goes on to be verified and fixed."""
    new = [f for f in ledger["findings"] if f["status"] == "found" and not f.get("root_cause")]
    if not new:
        return
    existing = "\n".join(f"- {rc}" for rc in root_causes(ledger)) or "- none"
    try:
        out = claude(prompt("group", EXISTING=existing, FINDINGS="\n".join(finding_text(f) for f in new)), READ_TOOLS)
    except Exception as e:
        log(f"grouping failed ({e}): every finding will be verified on its own")
        for f in new:
            f["root_cause"] = f"{f['function']}: {f['trigger'][:80]}"
        save_ledger(ledger)
        return
    by = {f["id"]: f for f in new}
    for g in out.get("groups", []):
        ids = [i for i in g.get("ids", []) if i in by]
        if not ids:
            continue
        rc = g.get("existing") or g.get("root_cause") or by[ids[0]]["function"]
        rep = g.get("representative") if g.get("representative") in ids else ids[0]
        for i in ids:
            by[i]["root_cause"] = rc
            if g.get("existing"):
                by[i]["status"] = "duplicate"  # already confirmed or fixed in an earlier round
            elif i != rep:
                by[i].update(status="duplicate", duplicate_of=rep)
    for f in new:  # anything the grouper left out is its own root cause
        f.setdefault("root_cause", f"{f['function']}: {f['trigger'][:80]}")
    save_ledger(ledger)
    log(f"grouped {len(new)} findings into {len({f['root_cause'] for f in new})} root causes")


def verify(ledger):
    confirmed = 0
    for f in [f for f in ledger["findings"] if f["status"] == "found"]:
        log(f"verifying {f['id']} ({f['root_cause'][:70]})")
        try:
            v = claude(prompt("verify", FINDING=finding_text(f), ID=f["id"].lower()), VERIFY_TOOLS)
        except Exception as e:
            log(f"  failed: {e}")
            continue
        f["status"] = v.get("verdict", "not_reproducible")
        f["verify_reason"] = v.get("reason", "")
        f["test_file"] = v.get("test_file")
        if f["status"] == "confirmed" and not suite_passes(only=[f["test_file"]], expect_fail=True):
            f["status"] = "not_reproducible"  # don't take the verifier's word: its test must actually fail now
            f["verify_reason"] = "test did not fail when rerun by the loop. " + f["verify_reason"]
        confirmed += f["status"] == "confirmed"
        log(f"  {f['status']}: {f['verify_reason']}")
        save_ledger(ledger)
    return confirmed


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def pending_tests(ledger, but=()):
    """Failing tests of confirmed bugs not fixed yet: left out of the gate, or no fix could ever pass it."""
    return [f["test_file"] for f in ledger["findings"]
            if f["status"] == "confirmed" and f.get("test_file") and f["test_file"] not in but]


def suite_cmd(ignore=()):
    return [sys.executable, "-m", "pytest", "-m", "not e2e", "-q"] + [f"--deselect={t}" for t in ignore]


def suite_passes(ignore=(), only=None, expect_fail=False):
    cmd = [sys.executable, "-m", "pytest", "-q", *only] if only else suite_cmd(ignore)
    ok = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).returncode == 0
    return not ok if expect_fail else ok


def fix(ledger, attempts=3):
    groups = {}
    for f in ledger["findings"]:
        if f["status"] == "confirmed":
            groups.setdefault(f["root_cause"], []).append(f)
    for rc, fs in groups.items():
        tests = [f["test_file"] for f in fs if f.get("test_file")]
        ignore = pending_tests(ledger, but=tests)
        cmd = " ".join(suite_cmd(ignore)).replace(sys.executable, "python3")
        for n in range(1, attempts + 1):
            log(f"fixing {rc[:70]} (attempt {n})")
            try:
                r = claude(prompt("fix", ROOT_CAUSE=rc, FINDINGS="\n".join(finding_text(f) for f in fs),
                                  TEST_FILES=", ".join(tests)).replace(
                    'python3 -m pytest -m "not e2e" -q', cmd), FIX_TOOLS)
            except Exception as e:
                r = {"status": "gave_up", "summary": str(e)}
            if r.get("status") == "fixed" and suite_passes(ignore):  # gate: don't trust the agent's word for it
                git("add", "-A", "landfinder.py", "site_template.html", "report_template.html", "functions", *tests)
                ids = ", ".join(f["id"] for f in fs)
                git("commit", "-q", "-m", f"{r.get('summary') or 'Fix ' + rc}\n\nRoot cause: {rc}\n"
                    f"Bughunt {ids}. Found and fixed by the bughunt loop (bughunt/findings.json).")
                commit = git("rev-parse", "--short", "HEAD").stdout.strip()
                for f in fs:
                    f.update(status="fixed", fix_summary=r.get("summary"), commit=commit)
                log(f"  fixed in {commit}")
                break
            git("checkout", "--", "landfinder.py", "site_template.html", "report_template.html", "functions")
        else:
            for f in fs:
                f["status"] = "needs_human"
            log("  gave up: left for you")
        save_ledger(ledger)


def main():
    cfg = json.load(open(os.path.join(HERE, "chunks.json")))
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--chunks", default=",".join(cfg["chunks"]))
    p.add_argument("--categories", default=",".join(cfg["categories"]))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--hunt-only", action="store_true")
    p.add_argument("--no-fix", action="store_true")
    a = p.parse_args()

    if git("branch", "--show-current").stdout.strip() in ("main", "master"):
        sys.exit("Run this on a branch, not main: git checkout -b bughunt")
    ledger = load_ledger()
    if not suite_passes(pending_tests(ledger)):
        sys.exit("The test suite fails before we start. Fix that first, or the gate means nothing.")
    for rnd in range(1, a.rounds + 1):
        log(f"=== round {rnd} ===")
        hunt(ledger, a.chunks.split(","), a.categories.split(","), a.workers)
        group(ledger)
        if a.hunt_only:
            continue
        confirmed = verify(ledger)
        if not a.no_fix:
            fix(ledger)
        if confirmed == 0:
            log("nothing confirmed this round: stopping")
            break

    counts = {}
    for f in ledger["findings"]:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    log("ledger: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
