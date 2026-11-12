// Password gate for the whole site: every page, data.json and the API answer only to a browser that has logged in.
//
// The password is the SITE_PASSWORD secret of the Pages project (`npx wrangler pages secret put SITE_PASSWORD`;
// locally, a line in .dev.vars). A right answer on /login sets a cookie holding an HMAC of a fixed string under the
// password, so the cookie is only valid while the password stays the same: change the password and everyone is
// logged out. Nothing is stored server side. Without SITE_PASSWORD set, the site refuses everyone rather than opening up.

const COOKIE = "land_session", DAYS = 30;

const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");

async function sessionToken(password){
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode("cheapest-land session v1")));
}

// compares in time that doesn't depend on where the strings first differ
function same(a, b){
  a = String(a ?? ""); b = String(b ?? "");
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

function cookieValue(request){
  const m = (request.headers.get("cookie") || "").match(new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]*)"));
  return m ? m[1] : "";
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function loginPage(next, error){
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cheapest Land</title><meta name="robots" content="noindex">
<style>
:root{color-scheme:light dark;--bg:#fff;--ink:#1b1b1f;--soft:#6b6b76;--tint:#f2f2f5;--line:#dcdce2;--accent:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#111114;--ink:#ececf1;--soft:#9a9aa6;--tint:#1c1c22;--line:#2a2a33;--accent:#8da2ff}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
form{width:min(360px,100%);display:grid;gap:12px}h1{font-size:22px;margin:0 0 4px}h1 span{font-weight:400;color:var(--soft)}
p{margin:0;color:var(--soft);font-size:14px}label{font-size:13px;color:var(--soft)}
input{width:100%;font:inherit;padding:10px 12px;border-radius:6px;border:1px solid var(--line);background:var(--tint);color:var(--ink)}
input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
button{font:inherit;font-weight:600;padding:10px 14px;border:0;border-radius:6px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:#d6453d;font-size:14px}
.legal{position:fixed;bottom:16px;left:0;right:0;text-align:center;font-size:12px}.legal a{color:var(--soft);margin:0 8px}
</style></head><body>
<form method="post" action="/login">
  <h1>Cheapest Land <span>worldwide</span></h1>
  <p>This site is private. Enter the password to continue.</p>
  <label for="pw">Password</label>
  <input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
  <input type="hidden" name="next" value="${esc(next)}">
  ${error ? `<div class="err">${esc(error)}</div>` : ""}
  <button type="submit">Enter</button>
</form>
<p class="legal"><a href="/privacy.html">Privacy</a><a href="/terms.html">Terms</a><a href="/licenses.html">Open source</a></p></body></html>`;
}

const html = (body, status, headers = {}) =>
  new Response(body, {status, headers: {"content-type": "text/html;charset=utf-8", "cache-control": "no-store", ...headers}});

// only a path on this site, never an outside URL, so the login form can't be used to send people elsewhere
const safeNext = p => (typeof p === "string" && /^\/(?!\/)/.test(p) && p !== "/login") ? p : "/";

// the Privacy, Terms and Open source pages (legal/, copied into site/ by build_site) are readable before logging in;
// Cloudflare Pages may serve them with or without ".html"
const PUBLIC = /^\/(privacy|terms|licenses)(\.html)?$|^\/legal\.css$/;

export async function onRequest({request, env, next}){
  const url = new URL(request.url);
  if (PUBLIC.test(url.pathname) && (request.method === "GET" || request.method === "HEAD")) return next();
  if (!env.SITE_PASSWORD) return html("<p>SITE_PASSWORD isn't set for this deployment.</p>", 500);
  const token = await sessionToken(env.SITE_PASSWORD);
  if (same(cookieValue(request), token)) {
    if (url.pathname === "/login") return Response.redirect(url.origin + "/", 303);
    return next();
  }
  if (url.pathname === "/login" && request.method === "POST") {
    const form = await request.formData();
    const to = safeNext(form.get("next"));
    if (same(form.get("password"), env.SITE_PASSWORD)) {
      return new Response(null, {status: 303, headers: {
        location: url.origin + to,
        "set-cookie": `${COOKIE}=${token}; Path=/; Max-Age=${DAYS * 86400}; HttpOnly; Secure; SameSite=Lax`,
        "cache-control": "no-store"}});
    }
    return html(loginPage(to, "That password isn't right."), 401);
  }
  if (url.pathname.startsWith("/api/")) return Response.json({error: "login required"}, {status: 401, headers: {"cache-control": "no-store"}});
  return html(loginPage(url.pathname === "/login" ? "/" : url.pathname + url.search, ""), 401);
}
