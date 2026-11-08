"""Build geo/admin1_points.tsv.gz from GeoNames' cities1000.txt and admin1CodesASCII.txt (CC BY 4.0): one line per
place with 1,000+ people: country, latitude, longitude (2 decimals) and the state, province or region it is in
("Ontario", "Kyiv Oblast"). The site's Province/state list gives each listing the province of its nearest place.

  curl -O https://download.geonames.org/export/dump/cities1000.zip && unzip cities1000.zip
  curl -O https://download.geonames.org/export/dump/admin1CodesASCII.txt
  .venv/bin/python tools/build_admin1.py cities1000.txt admin1CodesASCII.txt
"""
import gzip, os, sys

names = {}
with open(sys.argv[2], encoding="utf-8") as f:
    for line in f:
        code, name, ascii_name, _ = line.rstrip("\n").split("\t")
        names[code] = name

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geo", "admin1_points.tsv.gz")
seen = set()
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        c = line.rstrip("\n").split("\t")
        lat, lon, cc, adm = round(float(c[4]), 2), round(float(c[5]), 2), c[8], c[10]
        prov = names.get("%s.%s" % (cc, adm))
        if prov:
            seen.add((cc, lat, lon, prov))
with gzip.open(out, "wt", encoding="utf-8") as w:
    for row in sorted(seen):
        w.write("%s\t%s\t%s\t%s\n" % row)
print("%d places -> %s (%d KB)" % (len(seen), out, os.path.getsize(out) // 1024))
