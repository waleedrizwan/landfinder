"""Build geo/town_population.tsv.gz from GeoNames' cities1000.txt (every place with 1,000+ people, CC BY 4.0):
one line per place: country, state/province code (US and Canada only), population, then its names separated by "|"
(its own name, ASCII name and alternate names in Latin or Cyrillic letters). The site's City list is sorted with it.

  curl -O https://download.geonames.org/export/dump/cities1000.zip && unzip cities1000.zip
  .venv/bin/python tools/build_town_population.py cities1000.txt
"""
import gzip, os, re, sys

CA_PROVINCES = {"01": "AB", "02": "BC", "03": "MB", "04": "NB", "05": "NL", "07": "NS", "08": "ON", "09": "PE",
                "10": "QC", "11": "SK", "12": "YT", "13": "NT", "14": "NU"}
LETTERS = re.compile(r"^[\s\w.'’()-]+$")  # names in Latin or Cyrillic letters, not other scripts
FOREIGN_SCRIPT = re.compile(r"[^\u0000-ɏЀ-ӿḀ-ỿ\s.'’()-]")

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geo", "town_population.tsv.gz")
n = 0
with open(sys.argv[1], encoding="utf-8") as f, gzip.open(out, "wt", encoding="utf-8") as w:
    for line in f:
        c = line.rstrip("\n").split("\t")
        name, ascii_name, alt, cc, adm, pop = c[1], c[2], c[3], c[8], c[10], int(c[14] or 0)
        if not pop:
            continue
        state = adm if cc == "US" else CA_PROVINCES.get(adm, "") if cc == "CA" else ""
        names = [name, ascii_name] + [a for a in alt.split(",") if a and len(a) <= 40 and not FOREIGN_SCRIPT.search(a)]
        seen, keep = set(), []
        for x in names:
            if x.lower() not in seen:
                seen.add(x.lower())
                keep.append(x)
        w.write("%s\t%s\t%d\t%s\n" % (cc, state, pop, "|".join(keep)))
        n += 1
print("%d places -> %s (%d KB)" % (n, out, os.path.getsize(out) // 1024))
