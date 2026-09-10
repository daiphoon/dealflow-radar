const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const code = ts.transpileModule(fs.readFileSync(path.join(__dirname,
  "../components/watchlist-monitor.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const loaded = { exports: {} };
new Function("require", "module", "exports", code)(require, loaded, loaded.exports);
const render = (monitoring) => renderToStaticMarkup(React.createElement(
  loaded.exports.WatchlistMonitorStatus, { monitoring }));

test("关注页区分最近尝试、旧的成功时间和未来计划，不把失败写成成功", () => {
  const html = render({ status: "budget_deferred", categories: [],
    last_attempt_at: "2026-09-10T02:00:00Z", last_successful_check_at: "2026-09-01T02:00:00Z",
    next_check_at: "2026-09-12T02:00:00Z" });
  assert.match(html, /预算不足，已延后/);
  assert.match(html, /最近尝试：2026\/9\/10/);
  assert.match(html, /最近成功检查：2026\/9\/1 /);
  assert.match(html, /下次计划：2026\/9\/12/);
  assert.match(html, /经营、融资、合同\/中标/);
  assert.match(html, /未发现新资料不代表公司经营正常/);
});

test("未启用与尚未检查都有独立状态，不承诺自动执行时间", () => {
  assert.equal(render(null), "");
  const off = render({ status: "disabled", categories: [] });
  assert.match(off, /低频检查未启用/);
  assert.doesNotMatch(off, /下次计划/);
  const pending = render({ status: "not_scheduled", categories: [],
    last_attempt_at: null, last_successful_check_at: null, next_check_at: null });
  assert.match(pending, /等待安排首次检查/);
  assert.match(pending, /最近成功检查：暂无记录/);
});
