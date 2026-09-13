const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const ts = require("typescript");
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, "../lib/request-status.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const loaded = { exports: {} };
new Function("require", "module", "exports", code)(require, loaded, loaded.exports);
const state = (status, last_error_code = null, research_job_status = null) => loaded.exports.requestState({ status, last_error_code, research_job_status });

test("待补证、已人工确认和待核对不显示执行中，也不继续轮询", () => {
  assert.deepEqual(state("in_review", "identity_evidence_missing"), { label: "待补充主体资料", poll: false });
  assert.deepEqual(state("in_review", "curator_identity_confirmed"), { label: "主体已确认，待安排更新", poll: false });
  assert.deepEqual(state("in_review"), { label: "待人工核对", poll: false });
  for (const status of ["pending", "cancelled", "completed", "failed", "budget_deferred"]) assert.equal(state(status).poll, false);
});

test("只为排队或活动执行轮询，部分结果结束后停止", () => {
  for (const status of ["identity_queued", "identity_checking", "research_queued", "researching", "cancel_requested"]) assert.equal(state(status).poll, true);
  assert.equal(state("partial", null, "running").poll, true);
  assert.equal(state("partial", null, "completed").poll, false);
});
