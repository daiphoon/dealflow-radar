const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

function load(file, mocks) {
  const code = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(
    (id) => Object.hasOwn(mocks, id) ? mocks[id] : require(id), module, module.exports,
  );
  return module.exports;
}
const mocks = {
  "next/link": { default: ({ children, ...props }) => React.createElement("a", props, children) },
};
const pageDir = path.join(__dirname, "../app/watchlist");
const component = load(path.join(pageDir, "request-research-result.tsx"), mocks);
const request = (overrides = {}) => ({
  id: "demo-request", company_id: "demo-company", status: "completed",
  requested_name: "虚构申请测试公司", request_type: "refresh",
  created_at: "2026-09-01T02:00:00Z", updated_at: "2026-09-01T02:53:33Z",
  status_message: "当时未取得可展示的变化证据",
  current_company_content: { confirmed_changes: 0, baseline_facts: 0, unconfirmed_leads: 1 },
  research_modules: {
    financial_operation: "no_data", financing_cap_table: "no_data", contract_commercial: "no_data",
    product_technology: "no_data", governance_people: "no_data", legal_compliance: "no_data",
    capacity_assets: "no_data", exit_liquidity: "no_data", information_quality: "completed",
  },
  research_result: {
    finished_at: "2026-09-01T02:53:33Z", outcome: "no_usable_evidence",
    limitations: ["部分来源未通过自动读取规则检查", "本轮读取预算已用完，尚有资料未检查"],
    coverage_summary: ["记录了 2 次搜索调用；不代表各类信息已经逐项查全。", "当时取得或复用了 1 份正文。"],
  },
  ...overrides,
});
const render = (data) => renderToStaticMarkup(React.createElement(component.RequestResearchResult, { request: data }));
const visible = (html) => html.replace(/<details>.*?<\/details>/gs, "");

test("当前新增线索不被历史无成果覆盖；八句失败与信息质量伪成功不再展示", () => {
  const data = request();
  const before = JSON.stringify(data);
  const html = render(data);
  assert.match(visible(html), /待核实线索：1 条/);
  assert.match(visible(html), /本次资料覆盖有限/);
  assert.match(visible(html), /不能作为已确认结论/);
  assert.match(html, /<details><summary>查看本次查询过程与限制（历史记录）/);
  assert.doesNotMatch(visible(html), /当时未取得|8|读取预算/);
  assert.doesNotMatch(html, /本轮未取得可展示证据|信息质量|已取得资料|研究模块进度/);
  assert.match(html, /2026年9月1日 10:53/);
  assert.match(html, /2 次搜索/);
  assert.match(html, /尚有资料未检查/);
  assert.equal(JSON.stringify(data), before);
});

test("当前统计随新增和撤回变化，不从原任务统计推断事实", () => {
  for (const count of [0, 1, 1, 0]) {
    const html = render(request({ current_company_content: {
      confirmed_changes: 0, baseline_facts: 0, unconfirmed_leads: count,
    } }));
    assert.match(visible(html), new RegExp(`待核实线索：${count} 条`));
    assert.match(visible(html), count ? /当前尚无已核实事实/ : /当前暂无可展示的共享事实或线索/);
  }
  const html = render(request({ current_company_content: {
    confirmed_changes: 2, baseline_facts: 3, unconfirmed_leads: 1,
  } }));
  assert.match(visible(html), /已核实变化（含历史）：2 条；\s*基础资料：3 条；\s*待核实线索：1 条/);
});

test("旧接口未提供覆盖字段时保持未知，不显示假零值；无授权统计不显示", () => {
  const data = request({ current_company_content: null });
  delete data.research_result.coverage_summary;
  const html = render(data);
  assert.doesNotMatch(html, /当前可查看内容|0 次搜索|0 份正文/);
  assert.match(html, /检查情况未完整记录/);
});

test("运行中保留进度，未知状态不泄露内部字符串，信息质量不充当业务成果", () => {
  const html = render(request({ status: "partial", research_result: null,
    status_message: "正在核对正文", research_modules: {
      financial_operation: "search_completed", product_technology: "pending",
      governance_people: "internal-status", information_quality: "completed",
      private_module: "internal-status",
    } }));
  assert.match(html, /研究模块进度/);
  assert.match(html, /正在核对正文/);
  assert.match(html, /相关主题检索/);
  assert.match(html, /等待检查/);
  assert.doesNotMatch(html, /<details|已取得资料|private_module|internal-status|信息质量/);
});

test("取消和失败保留原因，不错误显示正在进行或八类无数据", () => {
  for (const status of ["cancelled", "failed", "rejected"]) {
    const html = render(request({ status, status_message: "已停止处理", research_result: null }));
    assert.match(html, /<details>/);
    assert.match(html, /已停止处理/);
    assert.doesNotMatch(html, /研究模块进度/);
  }
});

test("真实申请页接入当前摘要，保留取消操作，不额外逐公司调用详情接口", async () => {
  let calls = 0;
  const Page = load(path.join(pageDir, "page.tsx"), {
    ...mocks, "./request-research-result": component,
    "./request-status-refresher": { RequestStatusRefresher: () => null },
    "@/lib/auth-navigation": { redirectIfAuthenticationRequired: async () => {} },
    "@/app/personal-actions": {},
    "@/lib/api": {
      getPersonalWatchlist: async () => [],
      getPersonalCompanyRequests: async () => { calls++; return [request({ status: "partial", can_cancel: true })]; },
      getPersonalQuotaIncreaseRequests: async () => [],
      getPersonalUsage: async () => ({
        watchlist_companies: { used: 0, limit: 20 }, company_requests: { used: 0, limit: 30 },
        reports: { used: 0, limit: 10 }, searches: { used: 0, limit: 10 },
        daily_company_requests: { used: 0, limit: 10 },
      }),
    },
  }).default;
  const html = renderToStaticMarkup(await Page({ searchParams: Promise.resolve({}) }));
  assert.match(html, /当前可查看内容/);
  assert.match(html, /查看当前公司内容/);
  assert.match(html, /取消查询/);
  assert.equal(calls, 1);
});
