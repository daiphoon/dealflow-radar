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
    "@/components/research-coverage": load(path.join(__dirname, "../components/research-coverage.tsx"), {}),
    "@/components/tender-observations": load(path.join(__dirname, "../components/tender-observations.tsx"), {}),
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

test("公司详情展示本人的类别检查历史，不把检查成功计入当前事实", async () => {
  const data = company({ personal_research_result: { ...history, category_coverage: [{
    category: "contract_commercial", status: "evidence_obtained", route: "business_capital",
    evidence_count: 2, blocked_count: 1, failed_count: 0, gaps: ["仅取得正文，尚未核实"],
    last_attempt_at: null, search_checked_at: null, evidence_checked_at: null, cache_reused: false,
  }] } });
  const html = await render(data);
  assert.match(html, /本次类别来源覆盖/);
  assert.match(html, /相关正文 2 份 · 受阻 1 项/);
  assert.match(overview(html), /已核实变化（含历史）：0 条/);
  assert.ok(html.indexOf("当前可查看内容") < html.indexOf("本次类别来源覆盖"));
  assert.doesNotMatch(await render(company()), /本次类别来源覆盖/);
});

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

test("人工初始资料保留月份、历史、来源性质与未评分，不伪造最近检查", async () => {
  const version = { record_version: "revision", fact_version: "facts", is_current: true,
    date_text: "2024-04", date_precision: "月", date_basis: "公司回溯月份",
    reviewed_at: "2026-09-01T00:00:00Z", as_of_date: "2026-08-31",
    occurred_date_text: "未单独确认", subject_scope: "品牌融资；未确认法人增资",
    source_grade: "B｜媒体报道", content_support: "媒体报道支持", evidence_available: true,
    summary: "金额近一亿元，未推断估值" };
  const curated = { ...event("curated", "human_promoted"), occurred_at: null,
    materiality_score: 0, risk_severity: "unknown", confidence_score: "0",
    curated_versions: [version, { ...version, record_version: "old", is_current: false,
      summary: "早期人工记录，已保留" }] };
  const html = await render(company({ identity_verification_basis: "curator_confirmed", events: [curated] }));
  assert.match(html, /主体已由负责人确认/);
  assert.match(html, /资料所述日期：2024-04（月；公司回溯月份）/);
  assert.match(html, /人工整理、负责人已复核/);
  assert.match(html, /本次导入未重新读取网页/);
  assert.match(html, /尚未联网检查/);
  assert.match(html, /风险尚未评价/);
  assert.match(html, /品牌融资；未确认法人增资/);
  assert.match(html, /早期人工记录，已保留/);
  assert.doesNotMatch(html, /0\/100|>0%<|无显著风险|2024年4月1日/);
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

test("已核实中标进入变化区，日期只显示到日，历史版本保留，候选不进入变化数", async () => {
  const observation = { observation_id: null, fact_version: "new-version", observation_kind: "correction_candidate",
    occurred_on: "2026-08-03", date_precision: "day", observed_at: "2026-09-01T00:00:00Z",
    facts: [{ name: "中标金额", value: "12000000", unit: "CNY" }], evidence_ids: [],
    is_current: true, confirmed: true, evidence_available: true, can_publish: false };
  const tender = { ...event("tender", "human_promoted"), occurred_at: null, occurred_on: "2026-08-03",
    display_kind: "confirmed_change", fact_version: "new-version", tender_observations: [
      { ...observation, fact_version: "old-version", is_current: false,
        facts: [{ name: "中标金额", value: "12345000", unit: "CNY" }] }, observation,
    ] };
  const pending = { ...event("pending", "unconfirmed_lead"), display_kind: "unconfirmed", occurred_at: null,
    tender_observations: [{ ...observation, confirmed: false, occurred_on: null, date_precision: "unknown" }] };
  const html = await render(company({ events: [tender], unconfirmed_leads: [pending] }), [tender]);
  assert.match(overview(html), /已核实变化（含历史）：1 条/);
  assert.match(html, /当前已核实版本/);
  assert.match(html, /历史已核实版本/);
  assert.match(html, /12345000/);
  assert.match(html, /12000000/);
  assert.match(html, /未知（不以来源发布时间补填）/);
  assert.match(html, /转载数量不代表独立确认/);
});

test("撤回证据的历史仅显示状态，不泄露已隐藏的事实内容", () => {
  const { TenderObservations } = load(path.join(__dirname, "../components/tender-observations.tsx"), {});
  const html = renderToStaticMarkup(React.createElement(TenderObservations, { observations: [{
    fact_version: "revoked", observation_kind: "initial", occurred_on: null,
    is_current: false, confirmed: true, evidence_available: false,
    facts: [{ name: "撤回字段", value: "不应展示的值" }],
  }] }));
  assert.match(html, /证据已撤回或不可用/);
  assert.doesNotMatch(html, /不应展示的值|撤回字段/);
});

test("已有共享版本的审核页仍能核实新更正，表单只带所选观测的证据", async () => {
  const approved = { observation_id: "original-id", fact_version: "original", observation_kind: "initial",
    occurred_on: "2026-08-03", is_current: true, confirmed: false, evidence_available: true, can_publish: true,
    facts: [{ name: "中标金额", value: "12345000", unit: "CNY" }], evidence_ids: ["old-evidence"] };
  const revision = { ...approved, observation_id: "revision-id", fact_version: "revision", is_current: false,
    observation_kind: "correction_candidate", facts: [{ name: "中标金额", value: "12000000", unit: "CNY" }],
    evidence_ids: ["new-evidence"] };
  const mocks = {
    "next/link": { default: ({ children, ...props }) => React.createElement("a", props, children) },
    "@/components/tender-observations": load(path.join(__dirname, "../components/tender-observations.tsx"), {}),
    "@/lib/auth-navigation": { redirectIfAuthenticationRequired: async () => {} },
    "./actions": {},
    "@/lib/api": { ApiError: Error, getReviewWorkbench: async () => [],
      getPlatformQuotaIncreaseRequests: async () => [], getPlatformCompanyRequests: async () => [],
      getSharingCandidates: async () => [{ source_event_id: "source-id", shared_event_id: "shared-id",
        shared_event_status: "published", decisions: [{ source_observation_id: "original-id", action: "promote" }],
        event: { ...event("review"), tender_observations: [approved, revision], evidence: ["old-evidence", "new-evidence"].map(id => ({
          id, title: "虚构来源", source_name: "离线材料", url_health_status: "unchecked", excerpt: "原文摘录",
        })) } }],
    },
  };
  const Page = load(path.join(__dirname, "../app/reviews/page.tsx"), mocks).default;
  const html = renderToStaticMarkup(await Page({ searchParams: Promise.resolve({}) }));
  assert.match(html, /核实并采用此版本/);
  assert.match(html, /撤回共享事实/);
  const form = html.match(/<form[^>]*>.*?name="observation_id"[^>]*>.*?<\/form>/s)?.[0];
  assert.match(form, /value="revision-id"/);
  assert.match(form, /value="new-evidence"/);
  assert.doesNotMatch(form, /value="old-evidence"|value="original-id"/);
});

test("融资新来源和更正与初始事实分开展示，未知金额不显示虚构分数", async () => {
  const pending = { ...event("finance", "unconfirmed_lead"), display_kind: "unconfirmed", occurred_at: null,
    materiality_score: 0, confidence_score: "0", financing_observations: [{
      kind: "correction_candidate", fact_version: "finance-v2", fields: {
        subject_name: "示例品牌", subject_scope: "brand:示例品牌", round: "B轮", amount_text: null,
        investors: [], disclosed_on: "2026-09-01", occurred_on: null,
      }, issues: [], observed_at: "2026-09-02T00:00:00Z", evidence_id: "finance-evidence",
      source_url: "https://example.com/finance", source_title: "示例融资更正", excerpt: "示例融资原文片段", confirmed: false,
    }] };
  const html = await render(company({ platform_unconfirmed_leads: [pending] }), []);
  assert.match(html, /更正材料待核实/);
  assert.match(html, /品牌口径，不代表法人实收/);
  assert.match(html, /未知 \/ 未披露/);
  assert.match(html, /尚未评分/);
  assert.doesNotMatch(html, /0\/100|>0%/);
});
