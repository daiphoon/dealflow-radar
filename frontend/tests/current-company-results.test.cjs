const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// 用现有 TypeScript 编译器渲染真实页面，不安装新的测试框架，不访问网络。
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

const pageDir = path.join(__dirname, "../app/companies/[id]");
const event = (id, route = "manual") => ({
  id, title: `测试条目-${id}`, summary: "仅用于自动测试的虚构内容",
  publication_route: route, event_type: "product_technology", risk_severity: "low",
  materiality_score: 80, confidence_score: "0.9", source_quality: "A",
  occurred_at: "2026-09-01", published_at: "2026-09-01", observed_at: "2026-09-01",
  facts: [], fact_ledger: [], evidence: [], uncertainties: [], publication_reasons: [],
});
const history = {
  outcome: "no_usable_evidence", finished_at: "2026-08-01T02:03:00Z",
  message: "当时未取得可展示证据", limitations: ["来源不允许自动读取"],
};
const company = (overrides = {}) => ({
  id: "demo-company", legal_name: "统一展示测试公司（虚构）", is_platform_shared: true,
  identity_status: "verified", data_as_of: null, last_checked_at: null,
  freshness_status: "unknown", events: [], platform_unconfirmed_leads: [],
  private_events: [], unconfirmed_leads: [], investments: [], information_gaps: ["覆盖尚不完整"],
  personal_research_result: null, ...overrides,
});
async function render(data, newEvents = [], { failed = false, loading = false } = {}) {
  const view = { new_events: newEvents, first_view: true, viewed_at: "2026-09-07", previous_viewed_at: null };
  const mocks = {
    "next/link": { default: ({ children, ...props }) => React.createElement("a", props, children) },
    "@/app/personal-actions": {},
    "@/lib/auth-navigation": { redirectIfAuthenticationRequired: async () => {} },
    "@/lib/api": {
      getCompany: async () => data, getPersonalWatchlist: async () => [],
      getPersonalUsage: async () => ({ watchlist_companies: { used: 0, limit: 10 },
        company_requests: { used: 0, limit: 10 }, reports: { used: 0, limit: 10 } }),
    },
  };
  const panel = load(path.join(pageDir, "personal-change-panel.tsx"), {
    ...mocks,
    react: { ...React, useRef: () => ({ current: null }), useEffect: () => {},
      useMemo: (fn) => fn(), useState: (initial) => [initial === null ? (failed || loading ? null : view) : failed, () => {}] },
  });
  const Page = load(path.join(pageDir, "page.tsx"), { ...mocks, "./personal-change-panel": panel }).default;
  return renderToStaticMarkup(await Page({ params: Promise.resolve({ id: data.id }), searchParams: Promise.resolve({}) }));
}
const overview = (html) => html.match(/<section[^>]*aria-label="当前可查看内容"[^>]*>(.*?)<\/section>/s)?.[1];

test("历史无成果不覆盖后来新增的线索；查询历史默认折叠并位于内容之后", async () => {
  const data = company({ personal_research_result: history, platform_unconfirmed_leads: [event("lead")] });
  const before = JSON.stringify(data);
  const html = await render(data);
  assert.match(overview(html), /待核实线索：1 条/);
  assert.match(overview(html), /尚无已核实事实/);
  assert.match(html, /测试条目-lead/);
  assert.match(html, /<details><summary>查看我的最近一次查询过程（历史记录）/);
  assert.ok(html.indexOf("信息缺口") < html.indexOf("我的历史查询过程"));
  assert.match(html, /当时未取得可展示证据/);
  assert.match(html, /2026年8月1日 10:03/);
  assert.match(html, /尚无已发布资料/);
  assert.doesNotMatch(html, /我的最近一次查询结果/);
  assert.equal(JSON.stringify(data), before);
});

test("新增、去重回放、撤回后的当前接口列表驱动概览和内容，不使用旧任务统计", async () => {
  const data = company({ personal_research_result: history });
  assert.match(overview(await render(data)), /当前暂无可展示的共享事实或线索/);
  data.platform_unconfirmed_leads = [event("lead")];
  for (let replay = 0; replay < 2; replay++) {
    const html = await render(data);
    assert.match(overview(html), /待核实线索：1 条/);
    assert.equal(html.split("测试条目-lead").length - 1, 1);
  }
  data.platform_unconfirmed_leads = [];
  const html = await render(data);
  assert.match(overview(html), /待核实线索：0 条/);
  assert.doesNotMatch(html, /测试条目-lead/);
});

test("已核实变化、基础资料和线索分开统计，旧查看响应不能带回撤回的条目", async () => {
  const change = event("change", "deterministic_change");
  const baseline = event("baseline");
  const html = await render(company({ events: [change, baseline] }), [
    change, baseline, event("withdrawn", "deterministic_change"), event("removed-baseline"),
  ]);
  assert.match(overview(html), /已核实变化（含历史）：1 条/);
  assert.match(overview(html), /已核实基础资料：1 条/);
  assert.match(html, /另有 1 条新基础资料/);
  assert.match(html, /测试条目-change/);
  assert.match(html, /测试条目-baseline/);
  assert.doesNotMatch(html, /测试条目-withdrawn|测试条目-removed-baseline/);
});

test("机构私有叠加不计入共享概览，换为无授权响应后私有内容和他人历史均消失", async () => {
  const data = company({ private_events: [event("private")], unconfirmed_leads: [event("private-lead")],
    personal_research_result: history,
    investments: [{ fund_id: "fund", fund_name: "仅授权用户可见测试基金", amount: "123456", currency: "CNY", ownership: "0.2" }] });
  const own = await render(data);
  assert.match(overview(own), /已核实基础资料：0 条/);
  assert.match(overview(own), /待核实线索：0 条/);
  assert.match(own, /仅授权用户可见测试基金/);
  assert.match(own, /测试条目-private/);
  const other = await render(company());
  assert.doesNotMatch(other, /测试条目-private|仅授权用户可见测试基金|我的历史查询过程|当时未取得/);
});

test("私有公司不出现平台共享概览", async () => {
  assert.equal(overview(await render(company({ is_platform_shared: false }))), undefined);
});

test("个人水位加载中或失败不清空当前概览，失败回退仍只用当前列表", async () => {
  const change = event("change", "deterministic_change");
  for (const state of [{ loading: true }, { failed: true }]) {
    const html = await render(company({ events: [change] }), [], state);
    assert.match(overview(html), /已核实变化（含历史）：1 条/);
    assert.doesNotMatch(overview(html), /当前暂无可展示/);
    if (state.failed) assert.match(html, /测试条目-change/);
  }
});
