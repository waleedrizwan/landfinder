#!/usr/bin/env python3
"""Rebuild site/index.html (and the legal pages) from the existing site/data.json, without scraping.
Handy after editing site_template.html or legal/: run it, then serve site/ to look at the result.
  .venv/bin/python tools/rebuild_site.py
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import landfinder as lf  # noqa: E402

with open(os.path.join(lf.SITE, "data.json")) as f:
    data = json.load(f)
lf.write_site_pages(data.get("more", []))
print("rebuilt site/index.html (%d listings in data.json, %d more files)" % (len(data["rows"]), len(data.get("more", []))))
