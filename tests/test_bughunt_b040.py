import json, os, shutil, subprocess

import pytest

from conftest import ROOT

DRIVER = r"""
const mod = await import(process.argv[2]);
const db = {
  prepare(sql){ return { bind(){ return this; }, async run(){ return {meta:{changes:1}}; },
                         async first(){ return {n: 7}; } }; },
  async batch(stmts){ return stmts.map(() => ({results:[{salt:"s"}], meta:{changes:1}})); },
};
const request = new Request("https://x.test/api/visitors", {method:"POST", body:"null",
  headers:{"user-agent":"Mozilla/5.0", "content-type":"application/json"}});
try {
  const res = await mod.onRequestPost({request, env:{DB: db}});
  console.log(JSON.stringify({status: res.status, body: await res.json()}));
} catch (e) { console.log(JSON.stringify({threw: String(e)})); }
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_null_body_returns_total_not_error(tmp_path):
    drv = tmp_path / "drv.mjs"
    drv.write_text(DRIVER)
    src = os.path.join(ROOT, "functions", "api", "visitors.js")
    out = subprocess.run(["node", str(drv), "file://" + src], capture_output=True, text=True, timeout=60)
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert "threw" not in result, result
    assert result == {"status": 200, "body": {"total": 7}}
