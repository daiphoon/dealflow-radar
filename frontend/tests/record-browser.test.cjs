const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, "../components/record-browser.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const loaded = { exports: {} };
new Function("require", "module", "exports", code)(require, loaded, loaded.exports);
const { RecordBrowser, filterRecords } = loaded.exports;
const row = (id, occurred = "2026-09-20") => ({ id, category: "融资", status: "原文支持但未确认", occurred, content: React.createElement("article", null, `条目-${id}`) });

test("100 条记录第一页只呈现 20 条，明确总数和剩余页", () => {
  const records = Array.from({ length: 100 }, (_, i) => row(i.toString()));
  const html = renderToStaticMarkup(React.createElement(RecordBrowser, { records, reference: Date.parse("2026-09-24") }));
  assert.equal((html.match(/<article>/g) ?? []).length, 20);
  assert.match(html, /共 100 条，筛选后 100 条/);
  assert.match(html, /第 1 \/ 5 页/);
  assert.match(html, /下一页/);
  assert.doesNotMatch(html, /条目-20</);
});

test("类型、状态和发生时间联合筛选，未知日期不冒充近期", () => {
  const reference = Date.parse("2026-09-24");
  const records = [row("recent"), row("unknown", null), row("old", "2025-01-01"),
    { ...row("other"), category: "退出" }, { ...row("lead"), status: "待核实线索" }];
  assert.deepEqual(filterRecords(records, { category: "融资", status: "原文支持但未确认", period: "30" }, reference).map(r => r.id), ["recent"]);
  assert.deepEqual(filterRecords(records, { category: "", status: "", period: "unknown" }, reference).map(r => r.id), ["unknown"]);
  assert.equal(filterRecords(records, { category: "", status: "", period: "" }, reference).length, 5);
});
