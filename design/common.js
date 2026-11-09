// Helpers shared by the three mockups. Data comes from sample.js (42 real listings from site/data.json).
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const regionNames = new Intl.DisplayNames(["en"], {type: "region"});
const country = cc => { try { return regionNames.of(cc); } catch (e) { return cc; } };
const TYPES = {V:"Vacant land", L:"Building lot", F:"Farmland", T:"Forest", R:"Recreational", W:"Waterfront", C:"Commercial",
  G:"Dacha plot", B:"Land with house", H:"House"};
const SRC = {properstar:"Properstar", landwatch:"LandWatch", kijiji:"Kijiji", olx:"OLX", mercadolibre:"Mercado Libre"};
const CONTINENT = {};
for (const [name, codes] of Object.entries({
  "Africa": "DZ AO EG KE MA NA NG ZA TN TZ UG GH SN",
  "Asia": "AM AZ GE IN ID IL JP KZ KG MY MN PH TH TR AE UZ VN",
  "Europe": "AL AT BA BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT ME NL MK NO PL PT RO RS SK SI ES SE CH UA GB",
  "North America": "BZ CA CR DO GT HN MX NI PA PR US",
  "South America": "AR BO BR CL CO EC PY PE UY VE",
  "Oceania": "AU NZ FJ"}))
  for (const cc of codes.split(" ")) CONTINENT[cc] = name;

// listings written in capitals read as shouting: show them in sentence case
const tidy = s => {
  s = (s || "").trim().replace(/\s+/g, " ");
  const letters = s.replace(/[^\p{L}]/gu, ""), upper = s.replace(/[^\p{Lu}]/gu, "");
  if (letters.length > 6 && upper.length / letters.length > .6) s = s.toLowerCase().replace(/^\p{L}/u, c => c.toUpperCase());
  return s;
};
const usd = v => "$" + Math.round(v).toLocaleString("en-US");
const short = v => v < 1e3 ? "$" + Math.round(v) : v < 1e4 ? "$" + (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k" : "$" + Math.round(v / 1e3) + "k";
const acres = v => v < 1 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : Math.round(v).toLocaleString("en-US");
const m2 = v => Math.round(v * 4046.86).toLocaleString("en-US");
const town = r => (r.town || r.region.split(",")[0]).trim();
const place = r => `${town(r)}, ${country(r.cc)}`;
const ownPrice = r => r.cur && r.cur !== "USD" ? `${Math.round(r.price).toLocaleString("en-US")} ${r.cur}` : "";
const ROWS = window.D.rows.map(r => ({...r, title: tidy(r.title)}));
