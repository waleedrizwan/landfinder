// POST /api/visitors: the site's visitor count, for the line in the page footer.
//
// The page sends {first: true} the first time a browser visits (it remembers in localStorage that it was counted).
// A first visit adds one to the count, unless the same IP address and browser were already counted today: that stops
// a reload with storage blocked, or a script, from adding more than one a day.
// To tell, we keep a hash of the day's random salt + IP + user agent, never the IP itself. Salts and hashes older
// than two days are deleted, after which a hash can't be linked back to anyone.
//
// Needs a D1 database bound as DB (see wrangler.toml). The tables are created on first use.

const BOTS = /bot|crawl|spider|slurp|preview|headless|lighthouse|monitor|curl|wget|python|httpclient/i;
let ready = false;

async function setup(db){
  if (ready) return;
  await db.batch([
    db.prepare("CREATE TABLE IF NOT EXISTS total (id INTEGER PRIMARY KEY CHECK (id = 1), n INTEGER NOT NULL)"),
    db.prepare("INSERT OR IGNORE INTO total (id, n) VALUES (1, 0)"),
    db.prepare("CREATE TABLE IF NOT EXISTS salts (day TEXT PRIMARY KEY, salt TEXT NOT NULL)"),
    db.prepare("CREATE TABLE IF NOT EXISTS seen (day TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (day, hash))"),
  ]);
  ready = true;
}

const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");

async function countable(db, request){
  const ua = request.headers.get("user-agent") || "";
  if (!ua || BOTS.test(ua)) return false;
  const day = new Date().toISOString().slice(0, 10), old = new Date(Date.now() - 2 * 864e5).toISOString().slice(0, 10);
  const fresh = hex(crypto.getRandomValues(new Uint8Array(16)));
  const [, , , salt] = await db.batch([
    db.prepare("DELETE FROM salts WHERE day < ?").bind(old),
    db.prepare("DELETE FROM seen WHERE day < ?").bind(old),
    db.prepare("INSERT OR IGNORE INTO salts (day, salt) VALUES (?, ?)").bind(day, fresh),
    db.prepare("SELECT salt FROM salts WHERE day = ?").bind(day),
  ]);
  const ip = request.headers.get("cf-connecting-ip") || "";
  const hash = hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(salt.results[0].salt + "|" + ip + "|" + ua)));
  const res = await db.prepare("INSERT OR IGNORE INTO seen (day, hash) VALUES (?, ?)").bind(day, hash).run();
  return res.meta.changes === 1;
}

export async function onRequestPost({request, env}){
  const db = env.DB;
  if (!db) return Response.json({error: "No database is bound as DB"}, {status: 500});
  await setup(db);
  let body = {};
  try { body = await request.json(); } catch (e) {}
  if (body?.first === true && await countable(db, request)) await db.prepare("UPDATE total SET n = n + 1 WHERE id = 1").run();
  const row = await db.prepare("SELECT n FROM total WHERE id = 1").first();
  return Response.json({total: row.n}, {headers: {"cache-control": "no-store"}});
}
