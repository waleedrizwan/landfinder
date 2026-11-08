#!/usr/bin/env python3
"""Rebuild site/index.html from the existing site/data.json, without scraping.
Handy after editing site_template.html: run it, then serve site/ to look at the result.
  .venv/bin/python tools/rebuild_site.py
"""
import json, os, sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import landfinder as lf

with open(os.path.join(lf.SITE, "data.json")) as f:
    data = json.load(f)
rows = data["rows"]
lf.write_site(rows, {r[3] for r in rows}, datetime.fromisoformat(data["generated"]))
print("rebuilt site/index.html from %d listings" % len(rows))
