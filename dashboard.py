#!/usr/bin/env python3
"""Land Finder's dashboard: a window showing what a run is doing right now.

landfinder.py rewrites data/progress.json every 2 seconds while it runs (and opens this window unless --no-dashboard);
this reads it and draws it. No server: the two talk through that file. The Stop button writes data/stop, which ends
the run the way Ctrl-C does: the pages in progress finish, everything collected is saved, and the site is built.

    .venv/bin/python dashboard.py [data-dir]     # open it by hand, e.g. for a run started elsewhere
"""
import colorsys, json, os, sys, time
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
PROGRESS = os.path.join(DATA, "progress.json")
STALE_S = 15  # no update for this long (and not ended): the scraper is probably gone
SLOW_S = 45  # a window this long on one page is stuck or walled
BLOCK_HELP = ("A wall is a bot-check page in Chrome (Azure WAF, Cloudflare, Access Denied); http 403/429/503 is a refusal "
              "over plain HTTP; no data is a results page with no listings on it: a captcha or sign-in page, or a changed layout.")


def mins(s):
    if s is None:
        return "–"
    s = int(s)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm %02ds" % (s // 60, s % 60)
    return "%dh %02dm" % (s // 3600, s % 3600 // 60)


def short(url):
    return url.split("://", 1)[-1].replace("www.", "", 1)


def site_color(i, dark):
    r, g, b = colorsys.hls_to_rgb(((i * 67) % 360) / 360.0, 0.62 if dark else 0.45, 0.55)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


class Dashboard:
    def __init__(self, root):
        self.root = root
        root.title("Land Finder")
        root.geometry("1080x960")
        root.minsize(760, 600)
        self.dark = self._is_dark()
        self.ink = "systemTextColor" if self._has_system_colors() else ("#ecebe6" if self.dark else "#1d1d1b")
        self.paper = "systemWindowBackgroundColor" if self._has_system_colors() else ("#1e2026" if self.dark else "#ffffff")
        self.muted, self.bad, self.warn, self.accent = "#8a897f", "#d64540", "#d6823a", "#3f8f68"
        base = tkfont.nametofont("TkDefaultFont")
        self.f_big = tkfont.Font(family=base.cget("family"), size=22, weight="bold")
        self.f_num = tkfont.Font(family=base.cget("family"), size=20)
        self.f_small = tkfont.Font(family=base.cget("family"), size=11)
        self.f_h = tkfont.Font(family=base.cget("family"), size=11, weight="bold")
        self.f_mono = tkfont.nametofont("TkFixedFont").copy()
        self.f_mono.configure(size=11)
        self.stopped = False
        self.hover_run = None
        self._build()
        self.refresh()

    def _is_dark(self):
        try:
            import subprocess
            return subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True,
                                  timeout=3).stdout.strip() == "Dark"
        except Exception:
            return False

    def _has_system_colors(self):
        try:
            self.root.winfo_rgb("systemTextColor")
            return True
        except tk.TclError:
            return False

    # ---- layout

    def _build(self):
        r = self.root
        outer = ttk.Frame(r, padding=(14, 10, 14, 10))
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        self.stop = ttk.Button(head, text="Stop the run", command=self.ask_stop)
        self.stop.pack(side="right", padx=(12, 0))
        ttk.Label(head, text="Land Finder", font=self.f_big).pack(side="left")
        self.phase = ttk.Label(head, text="starting", font=self.f_h, foreground=self.accent)
        self.phase.pack(side="left", padx=(14, 10), pady=(8, 0))
        self.meta = ttk.Label(head, text="", font=self.f_small, foreground=self.muted)
        self.meta.pack(side="left", pady=(8, 0), fill="x", expand=True)

        self.gaveup = tk.Label(outer, text="", anchor="w", justify="left", wraplength=1000, padx=10, pady=6,
                               bg="#48301b" if self.dark else "#f6e3cf", fg="#f0c391" if self.dark else "#7a3d0f")

        self.bar = ttk.Progressbar(outer, maximum=100, value=0)
        self.bar.pack(fill="x", pady=(10, 2))
        self.jobs = ttk.Label(outer, text="", font=self.f_small, foreground=self.muted)
        self.jobs.pack(fill="x")

        stats = ttk.Frame(outer)
        stats.pack(fill="x", pady=(10, 4))
        self.stat = {}
        for key, caption in (("pages", "pages fetched"), ("rate", "pages in the last minute"), ("new", "new listings this run"),
                             ("disk", "listings on disk"), ("failed", "failed pages"), ("blocks", "blocks and bot checks")):
            box = ttk.Frame(stats, padding=(0, 0, 24, 0))
            box.pack(side="left")
            num = ttk.Label(box, text="0", font=self.f_num)
            num.pack(anchor="w")
            cap = ttk.Label(box, text=caption, font=self.f_small, foreground=self.muted)
            cap.pack(anchor="w")
            self.stat[key] = (num, cap)

        self._heading(outer, "Windows and threads")
        self.workers = self._table(outer, (("name", "", 60), ("pages", "pages", 60), ("since", "on this page", 95),
                                           ("label", "doing", 330), ("url", "page", 420)), height=5)
        self.workers.tag_configure("slow", foreground=self.warn)
        self.workers.tag_configure("idle", foreground=self.muted)

        self._heading(outer, "Blocks and bot checks, by run")
        self.chart = tk.Canvas(outer, height=96, highlightthickness=0, bg=self.paper)
        self.chart.pack(fill="x")
        self.chart.bind("<Motion>", self.on_chart_hover)
        self.chart.bind("<Leave>", lambda e: self._set_chart_note(None))
        self.chart_note = ttk.Label(outer, text="", font=self.f_small, foreground=self.muted)
        self.chart_note.pack(fill="x")

        self._heading(outer, "Blocks this run")
        self.blocks = self._table(outer, (("time", "time", 70), ("source", "site", 90), ("via", "via", 60), ("how", "kind", 70),
                                          ("detail", "what the site sent", 260), ("worker", "thread", 60), ("url", "page", 380)), height=4)
        self.blocks.tag_configure("wall", foreground=self.bad)
        self.blocks.tag_configure("http", foreground=self.warn)
        self.blocks.tag_configure("help", foreground=self.muted)
        ttk.Label(outer, text=BLOCK_HELP, font=self.f_small, foreground=self.muted, wraplength=1040, justify="left").pack(anchor="w", pady=(3, 0))

        self._heading(outer, "Sites")
        self.sites = self._table(outer, (("source", "site", 110), ("via", "via", 60), ("pages", "pages", 60), ("recent", "last min", 65),
                                         ("verify", "verify", 55), ("failed", "failed", 55), ("blocked", "blocked", 60), ("passed", "got past", 60),
                                         ("spp", "s / page", 60), ("new", "new listings", 90)), height=6)
        self.sites.tag_configure("bad", foreground=self.bad)

        self._heading(outer, "Log")
        logf = ttk.Frame(outer)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, font=self.f_mono, wrap="word", height=8, state="disabled", relief="flat",
                           bg=self.paper, fg=self.ink, highlightthickness=0, padx=8, pady=6)
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log_hover = False  # the log follows the run unless the pointer is over it (someone is reading)
        self.log.bind("<Enter>", lambda e: setattr(self, "log_hover", True))
        self.log.bind("<Leave>", lambda e: setattr(self, "log_hover", False))

    def _heading(self, parent, text):
        ttk.Label(parent, text=text.upper(), font=self.f_small, foreground=self.muted).pack(anchor="w", pady=(12, 3))

    def _table(self, parent, cols, height):
        tv = ttk.Treeview(parent, columns=[c[0] for c in cols], show="headings", height=height, selectmode="none")
        for key, title, width in cols:
            tv.heading(key, text=title, anchor="w")
            tv.column(key, width=width, minwidth=40, anchor="w", stretch=(key in ("label", "url", "detail", "source")))
        tv.pack(fill="x")
        return tv

    def _fill(self, tv, rows):
        """rows: list of (values, tags)."""
        tv.delete(*tv.get_children())
        for values, tags in rows:
            tv.insert("", "end", values=values, tags=tags)

    # ---- data

    def read(self):
        try:
            with open(PROGRESS) as f:
                d = json.load(f)
            return d, time.time() - os.path.getmtime(PROGRESS)
        except (OSError, ValueError):
            return None, None

    def refresh(self):
        d, age = self.read()
        if d is None:
            self.phase.configure(text="waiting for landfinder to start", foreground=self.muted)
            self.meta.configure(text="no %s yet" % os.path.relpath(PROGRESS, HERE))
        else:
            self.render(d, age)
        self.root.after(2000, self.refresh)

    def render(self, d, age):
        ended = d.get("ended") or (age is not None and age > STALE_S)
        if d.get("stopping") and not ended:
            ph, color = "stopping", self.muted
        elif ended:
            ph, color = ("ended" if d.get("ended") else "no update for %s: the scraper is gone?" % mins(age)), self.muted
        else:
            ph, color = (d.get("phase") or "between phases"), self.accent if d.get("phase") else self.muted
        self.phase.configure(text=ph, foreground=color)
        meta = "started %s · %s elapsed" % (d["started"], mins(d["elapsed_s"]))
        if d.get("eta_s") and not ended:
            meta += " · roughly %s of searching left" % mins(d["eta_s"])
        if d.get("args"):
            meta += " · " + d["args"]
        self.meta.configure(text=meta if len(meta) < 150 else meta[:147] + "…")

        g = d.get("given_up") or []
        if g:
            self.gaveup.configure(text="Given up on %s for this run: %s blocked %d pages in a row, so the rest of %s pages are "
                                       "skipped. Listings already on disk stay on the site; the next run tries again."
                                       % (", ".join(g), "they" if len(g) > 1 else "it", d.get("give_up_after") or 3,
                                          "their" if len(g) > 1 else "its"))
            self.gaveup.pack(fill="x", pady=(8, 0), after=self.phase.master)
        else:
            self.gaveup.pack_forget()

        j, step = d["jobs"], d.get("step")
        pct = round(100.0 * j["done"] / j["total"]) if j["total"] else 0
        searched = "%d of %d searches done (%d%%)" % (j["done"], j["total"], pct)
        if step and not ended:  # the searches are over: the bar follows the step that's running now
            n, total = step.get("done"), step.get("total")
            self.bar.configure(value=round(100.0 * n / total) if total else 0, mode="determinate")
            progress = " · %d of %d" % (n, total) if total else ""
            self.jobs.configure(text="Now %s%s · %s running. %s." % (step["text"], progress, mins(step["since_s"]),
                                                                    "All searches done" if j["done"] >= j["total"] else searched))
        else:
            self.bar.configure(value=pct)
            self.jobs.configure(text="%s: a search is one site's land or house section in one country, state or province"
                                     % searched if j["total"] else "no browser searches queued yet")

        S = d["sites"]
        total = lambda k: sum(s.get(k, 0) for s in S)
        self._stat("pages", total("pages"))
        self._stat("rate", total("recent"), None, "pages in the last minute" + (" (searches done)" if step else ""))
        self._stat("new", "{:,}".format(d["listings_new"]))
        self._stat("disk", "–" if d.get("on_disk") is None else "{:,}".format(d["on_disk"]))
        self._stat("failed", total("failed"), self.bad if total("failed") else None)
        blocked, passed = total("blocked"), total("passed")
        self._stat("blocks", blocked, self.bad if blocked else None,
                   "bot checks · got past %d" % passed if blocked else "bot checks: none so far")

        rows = []
        for w in d["workers"]:
            if w["busy"]:
                since = "%ds" % w["since_s"] + (" · verifying" if w.get("what") == "verify" else "")
                tags = ("slow",) if w["since_s"] > SLOW_S else ()
                label = short(w["url"]) if w["label"] == w["url"] else w["label"]
                rows.append(((w["name"], w["pages"], since, label, short(w["url"])), tags))
            else:
                rows.append(((w["name"], w["pages"], "", w["label"], ""), ("idle",)))
        if step and not ended:  # what the main thread is doing, above the finished windows
            rows.insert(0, (("main", "", mins(step["since_s"]), step["text"] + (" · %d of %d" % (step["done"], step["total"])
                                                                                 if step.get("total") else ""), ""), ()))
        self._fill(self.workers, rows or [(("", "", "", "nothing fetching yet", ""), ("idle",))])

        self.draw_chart(d.get("history") or [])

        rows = [((b["time"], b["source"], b["via"], b["how"], b["detail"] or "(no title)", b["worker"], short(b["url"])),
                 ("wall",) if b["how"] == "wall" else ("http",) if b["how"].startswith("http") else ())
                for b in d.get("blocks") or []]
        self._fill(self.blocks, rows or [(("", "", "", "", "no blocks so far this run", "", ""), ("help",))])

        rows = [((s["source"], s["via"], s["pages"], s["recent"], s["verify"], s["failed"], s["blocked"], s["passed"],
                  "%.1f" % (s["secs"] / s["pages"]) if s["pages"] else "–", "{:,}".format(s["new"])),
                 ("bad",) if s["failed"] or s["blocked"] else ()) for s in S]
        self._fill(self.sites, rows or [(("no pages yet",) + ("",) * 9, ())])

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(d["log"]))
        self.log.configure(state="disabled")
        if not self.log_hover:
            self.log.see("end")

        self.stop.configure(state="disabled" if (d.get("stopping") or ended or self.stopped) else "normal",
                            text="Stopping…" if d.get("stopping") and not ended else "Stop the run")

    def _stat(self, key, value, color=None, caption=None):
        num, cap = self.stat[key]
        num.configure(text=str(value), foreground=color or self.ink)
        if caption:
            cap.configure(text=caption)

    # ---- the per-run chart

    def draw_chart(self, history):
        c = self.chart
        c.delete("all")
        self.history = history
        w, h = max(c.winfo_width(), 200), int(c.cget("height"))
        names = sorted({k for r in history for k in r.get("blocks", {})})
        self.site_names = names
        top = max([1] + [sum(r.get("blocks", {}).values()) for r in history])
        n = max(len(history), 1)
        gap, pad = 4, 8
        colw = min(40, max(8, (w - 2 * pad - gap * (n - 1)) // n))
        self.cols = []
        x = pad
        for r in history:
            total = sum(r.get("blocks", {}).values())
            y = h - 24
            if not total:
                c.create_rectangle(x, y - 2, x + colw, y, fill="#555" if self.dark else "#ddd", outline="")
            for k in names:
                v = r.get("blocks", {}).get(k)
                if v:
                    hh = max(2, (h - 34) * v / top)
                    c.create_rectangle(x, y - hh, x + colw, y, fill=site_color(names.index(k), self.dark), outline="")
                    y -= hh
            if r.get("live"):
                c.create_rectangle(x - 2, 6, x + colw + 2, h - 22, outline=self.accent, dash=(3, 2))
            self.cols.append((x, x + colw, r))
            x += colw + gap
        lx = pad
        for i, k in enumerate(names):
            c.create_rectangle(lx, h - 14, lx + 10, h - 4, fill=site_color(i, self.dark), outline="")
            t = c.create_text(lx + 14, h - 9, text=k, anchor="w", font=self.f_small, fill=self.muted)
            lx = c.bbox(t)[2] + 14
        c.create_text(w - pad, h - 9, anchor="e", font=self.f_small, fill=self.muted,
                      text="%d run%s · hover a column · dashed = this run" % (len(history), "" if len(history) == 1 else "s")
                      if history else "no runs recorded yet")

    def on_chart_hover(self, e):
        for x0, x1, r in getattr(self, "cols", []):
            if x0 <= e.x <= x1:
                return self._set_chart_note(r)
        self._set_chart_note(None)

    def _set_chart_note(self, r):
        if not r:
            return self.chart_note.configure(text="")
        total, pages = sum(r.get("blocks", {}).values()), sum(r.get("pages", {}).values())
        text = "%s%s: %d block%s in %d pages" % (r["start"].replace("T", " "), " (this run, so far)" if r.get("live") else "",
                                                 total, "" if total == 1 else "s", pages)
        if total:
            text += " · " + ", ".join("%s %d" % kv for kv in r["blocks"].items())
        if r.get("gave_up"):
            text += " · gave up on " + ", ".join(r["gave_up"])
        if r.get("args"):
            text += " · " + r["args"]
        self.chart_note.configure(text=text)

    # ---- stop

    def ask_stop(self):
        if not messagebox.askyesno("Stop the run?", "Pages in progress finish, everything collected so far is saved, and the "
                                                    "site is built from it.", parent=self.root):
            return
        try:
            with open(os.path.join(DATA, "stop"), "w") as f:
                f.write("stop\n")
            self.stopped = True
            self.stop.configure(state="disabled", text="Stopping…")
        except OSError as e:
            messagebox.showerror("Couldn't ask the run to stop", str(e), parent=self.root)


def main():
    os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
