import { randomBytes } from "node:crypto";

import Link from "next/link";
import { ResearchCoverage } from "@/components/research-coverage";
import { TenderObservations } from "@/components/tender-observations";

import {
  followCompany,
  generateCompanyReport,
  requestCompanyRefresh,
  unfollowCompany,
} from "@/app/personal-actions";
import {
  getCompany,
  getPersonalUsage,
  getPersonalWatchlist,
  type Event,
  type Investment,
} from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

import { PersonalChangePanel } from "./personal-change-panel";

export const dynamic = "force-dynamic";

const eventTypeLabels: Record<string, string> = {
  financial_operation: "财务与经营",
  financing_cap_table: "融资与股权",
  contract_commercial: "合同与商业化",
  product_technology: "产品与技术",
  governance_people: "治理与人员",
  legal_compliance: "司法与合规",
  capacity_assets: "产能与资产",
  exit_liquidity: "退出与流动性",
  information_quality: "信息质量",
};

const riskLabels: Record<string, string> = {
  none: "无显著风险",
  low: "低风险",
  moderate: "中等风险",
  high: "高风险",
  critical: "严重风险",
};

const freshnessLabels: Record<string, string> = {
  fresh: "数据新鲜",
  stale: "数据已过期",
  refreshing: "后台更新中",
  unknown: "检查状态未确认",
};

const publicationReasonLabels: Record<string, string> = {
  auto_publish_disabled: "自动发布策略已关闭",
  source_url_unchecked: "来源链接尚未检查",
  source_url_broken: "来源链接失效",
  source_url_unavailable: "来源链接当前无法访问",
  source_quality_not_allowed: "来源等级不足",
  confidence_below_threshold: "可信度低于阈值",
  high_risk_unconfirmed: "属于高风险信息",
  official_website_not_verified: "公司官网尚未核验",
  official_source_domain_mismatch: "来源域名与核验官网不一致",
  licensed_source_risk_record_requires_review: "授权来源中的风险记录仍需核对",
  subject_identity_verified: "公司工商主体已经核验",
  licensed_source_record: "资料来自已授权数据源",
};

const factSupportLabels: Record<string, string> = {
  supported: "证据已支持",
  partial: "证据部分支持",
  conflicting: "证据存在冲突",
  pending_review: "等待逐条复核",
  unsupported: "现有证据未支持",
};

function formatDate(value: string | null, includeTime = false): string {
  if (!value) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: includeTime ? "short" : undefined,
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatMoney(value: string | null, currency: string | null): string {
  if (!value || !currency) return "未披露";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function formatOwnership(value: string | null): string {
  if (!value) return "未披露";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function identityLabel(status: string, basis: string | null): string {
  if (status !== "verified") return "待核验";
  if (basis === "official_government") return "已核验（政府官方来源）";
  if (basis === "exchange_disclosure") return "已核验（交易所披露·人工核验）";
  if (basis === "public_crosscheck") return "主体已交叉核对（公开资料，非官方登记核验）";
  if (basis === "curator_confirmed") return "主体已由负责人确认";
  return "待核验";
}

function InvestmentCard({ investment }: { investment: Investment }) {
  return (
    <article className="investment-card">
      <div>
        <p className="eyebrow">{investment.fund_name}</p>
        <h3>{investment.round_name}</h3>
      </div>
      <dl className="metric-list">
        <div>
          <dt>投资金额</dt>
          <dd>{formatMoney(investment.amount, investment.currency)}</dd>
        </div>
        <div>
          <dt>持股比例</dt>
          <dd>{formatOwnership(investment.ownership)}</dd>
        </div>
        <div>
          <dt>内部估值</dt>
          <dd>{formatMoney(investment.internal_valuation, investment.currency)}</dd>
        </div>
      </dl>
      <p className="privacy-note">基金私密数据 · 当前测试身份已授权</p>
    </article>
  );
}

function EventCard({
  event,
  unconfirmed = false,
  privateRecord = false,
}: {
  event: Event;
  unconfirmed?: boolean;
  privateRecord?: boolean;
}) {
  const eventDate = event.occurred_on ?? event.occurred_at ?? event.published_at ?? event.published_on;
  const isTender = Boolean(event.tender_observations?.length);
  const curatedVersions = event.curated_versions ?? [];
  const isCurated = curatedVersions.length > 0;
  const curated = curatedVersions.find((item) => item.is_current) ?? curatedVersions[0];
  const isLicensedSourceRecord = event.publication_route === "licensed_source_record";
  const isDeterministicChange = event.publication_route === "deterministic_change";
  const isConfirmedChange = event.display_kind === "confirmed_change" || isDeterministicChange;
  const changeField = event.facts.find((fact) => fact.name === "变化字段")?.value;
  const beforeValue = event.facts.find((fact) => fact.name === "变更前")?.value;
  const afterValue = event.facts.find((fact) => fact.name === "变更后")?.value;
  const publicationLabel = unconfirmed
    ? "未确认线索"
    : isCurated
      ? "人工整理、负责人已复核"
    : privateRecord
      ? "机构私有已确认"
      : event.publication_route === "licensed_structured_fact"
        ? "授权来源已核实事实"
        : isDeterministicChange
          ? "程序核验的重要变化"
        : isLicensedSourceRecord
          ? "授权来源记录·影响待判断"
        : event.publication_route === "auto_published"
          ? "规则自动发布"
          : "人工或历史确认";
  return (
    <article className="event-card">
      <div className="event-meta">
        <span>{eventTypeLabels[event.event_type] ?? event.event_type}</span>
        <span>
          {isCurated
            ? `资料所述日期：${curated.date_text || "未知"}（${curated.date_precision}；${curated.date_basis}）`
            : isTender
            ? `公告所述事件日期：${event.occurred_on ?? "未知"}${unconfirmed ? "（待核实）" : ""}`
            : unconfirmed
            ? event.occurred_at
              ? `正文所述事件日期：${formatDate(event.occurred_at)}（待核实）`
              : `事件日期待核实 · 来源发布：${formatDate(eventDate)}（不代表近期发生）`
            : formatDate(eventDate)}
        </span>
        <span
          className={`risk ${isLicensedSourceRecord || isCurated ? "risk-unknown" : `risk-${event.risk_severity}`}`}
        >
          {isCurated
            ? "风险尚未评价"
            : isLicensedSourceRecord
            ? "影响待判断"
            : (riskLabels[event.risk_severity] ?? event.risk_severity)}
        </span>
      </div>
      <h3>{event.title}</h3>
      <p>{event.summary}</p>
      {isCurated ? (
        <section aria-label="人工整理资料口径" className="privacy-note">
          <p>{unconfirmed ? "人工整理记录 · 仍待核实" : "人工整理、负责人已复核"} · 本次导入未重新读取网页。</p>
          <p>人工确认：{formatDate(curated.reviewed_at)} · 资料基准日：{curated.as_of_date || "未知"}</p>
          <p>实际发生日期：{curated.occurred_date_text || "未知"} · {curated.subject_scope}</p>
          <p>原资料证据等级：{curated.source_grade} · {curated.content_support}。金额与近似口径按原资料保留。</p>
          {curatedVersions.length > 1 ? (
            <details>
              <summary>查看人工资料历史版本（{curatedVersions.length}）</summary>
              {curatedVersions.map((version) => (
                <div key={version.record_version}>
                  <strong>{version.is_current ? "当前版本" : "历史版本"} · {formatDate(version.reviewed_at)}</strong>
                  <p>{version.evidence_available ? version.summary : "该版本证据已撤回或不可用"}</p>
                </div>
              ))}
            </details>
          ) : null}
        </section>
      ) : null}
      {event.fact_ledger.length > 0 ? (
        <section className="fact-support-ledger" aria-label="事实与证据支持情况">
          <div className="fact-support-heading">
            <strong>事实与证据支持情况</strong>
            <span>{isCurated ? "支持依据为人工整理记录；不表示系统已读取网页原文" : "逐条判断，不以“有链接”代替“证据支持”"}</span>
          </div>
          <dl>
            {event.fact_ledger.map((fact) => (
              <div key={fact.id}>
                <dt>{fact.name}</dt>
                <dd>
                  <strong>
                    {fact.value}
                    {fact.unit ? ` ${fact.unit}` : ""}
                  </strong>
                  <span className={`fact-support-status status-${fact.support_status}`}>
                    {factSupportLabels[fact.support_status] ?? fact.support_status}
                  </span>
                  <small>
                    {fact.evidence_supports.length > 0
                      ? `${fact.evidence_supports.length} 条当前可见证据已逐条记录`
                      : "当前没有可见证据支持这条事实"}
                  </small>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}
      {isDeterministicChange && changeField && beforeValue && afterValue ? (
        <div className="change-comparison" aria-label={`${changeField}前后变化`}>
          <span>{changeField}</span>
          <div>
            <p>
              <small>变更前</small>
              <strong>{beforeValue}</strong>
            </p>
            <span aria-hidden="true">→</span>
            <p>
              <small>变更后</small>
              <strong>{afterValue}</strong>
            </p>
          </div>
        </div>
      ) : null}
      <dl className="score-grid">
        <div>
          <dt>重要性</dt>
          <dd>{isCurated ? "尚未评分" : `${event.materiality_score}/100`}</dd>
        </div>
        <div>
          <dt>可信度</dt>
          <dd>{isCurated ? "尚未评分" : `${Math.round(Number(event.confidence_score) * 100)}%`}</dd>
        </div>
        <div>
          <dt>来源质量</dt>
          <dd>{event.source_quality} 级</dd>
        </div>
        <div>
          <dt>发布状态</dt>
          <dd>{publicationLabel}</dd>
        </div>
      </dl>
      {unconfirmed ? (
        <p className="privacy-note">
          该信息由系统自动保留，尚未升级为已确认事实，也不会进入公司风险结论或快照。
          {event.publication_reasons.length > 0
            ? ` 原因：${Array.from(new Set(event.publication_reasons
                .map((reason) => publicationReasonLabels[reason] ?? "仍需进一步核实")))
                .join("、")}。`
            : ""}
        </p>
      ) : null}
      {unconfirmed && event.research_analysis ? (
        <section className="investor-analysis" aria-label="待核实的模型辅助解读">
          <div className="analysis-heading">
            <div>
              <p className="eyebrow">模型辅助整理 · 仍待核实</p>
              <h4>{event.research_analysis.headline}</h4>
            </div>
            <span>
              解读可信度 {Math.round(Number(event.research_analysis.confidence) * 100)}%
            </span>
          </div>
          <p>{event.research_analysis.what_changed}</p>
          <div>
            <strong>为什么值得留意</strong>
            <p>{event.research_analysis.why_it_matters}</p>
          </div>
          {event.research_analysis.potential_impacts.length > 0 ? (
            <div>
              <strong>可能影响</strong>
              <ul>
                {event.research_analysis.potential_impacts.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {event.research_analysis.uncertainties.length > 0 ? (
            <div>
              <strong>目前还不能确定</strong>
              <ul>
                {event.research_analysis.uncertainties.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {event.research_analysis.follow_up_items.length > 0 ? (
            <div>
              <strong>后续值得关注</strong>
              <ul>
                {event.research_analysis.follow_up_items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="analysis-disclaimer">{event.research_analysis.disclaimer}</p>
        </section>
      ) : null}
      {isLicensedSourceRecord ? (
        <p className="privacy-note">
          这是授权数据源已返回的记录概览，可供查看；平台尚未将数量、关联关系或评分解释为风险结论。
        </p>
      ) : null}
      {isConfirmedChange ? (
        event.analysis ? (
          <section className="investor-analysis" aria-label="模型辅助解读">
            <div className="analysis-heading">
              <div>
                <p className="eyebrow">模型辅助解读</p>
                <h4>{event.analysis.headline}</h4>
              </div>
              <span>可信度 {Math.round(Number(event.analysis.confidence) * 100)}%</span>
            </div>
            <p>{event.analysis.what_changed}</p>
            <div>
              <strong>为什么值得关注</strong>
              <p>{event.analysis.why_it_matters}</p>
            </div>
            {event.analysis.potential_impacts.length > 0 ? (
              <div>
                <strong>可能影响</strong>
                <ul>
                  {event.analysis.potential_impacts.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {event.analysis.uncertainties.length > 0 ? (
              <div>
                <strong>仍需注意</strong>
                <ul>
                  {event.analysis.uncertainties.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {event.analysis.follow_up_items.length > 0 ? (
              <div>
                <strong>后续观察</strong>
                <ul>
                  {event.analysis.follow_up_items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            <p className="analysis-disclaimer">{event.analysis.disclaimer}</p>
          </section>
        ) : (
          <p className="analysis-pending">
            变化事实已核实；辅助解读尚未生成，不影响查看原始变化和证据。
          </p>
        )
      ) : null}
      <TenderObservations observations={event.tender_observations ?? []} />
      {event.evidence.map((evidence) => {
        const sourceUrl = evidence.final_url ?? evidence.canonical_url;
        const sourceAvailable = evidence.link_display_allowed;
        return (
          <div className="evidence" key={evidence.id}>
            <div>
              <span className="eyebrow">证据 · {evidence.source_name}</span>
              <strong>{evidence.title}</strong>
            </div>
            <blockquote>{evidence.excerpt}</blockquote>
            {evidence.detail_available ? (
              <Link href={`/evidence/${encodeURIComponent(evidence.id)}`}>
                查看平台证据详情
              </Link>
            ) : null}
            {sourceAvailable ? (
              <a href={sourceUrl} rel="noreferrer" target="_blank">
                {evidence.link_kind === "licensed_provider"
                  ? "查看供应商原始页面 ↗"
                  : "查看公开来源链接 ↗"}
              </a>
            ) : (
              <span className="muted">
                {evidence.url_health_status === "broken"
                  ? "来源链接已失效"
                  : evidence.link_kind === "unavailable" && evidence.detail_available
                    ? "供应商原始页面暂不可直接定位"
                    : "来源链接不可开放"}
                {evidence.url_http_status ? `（HTTP ${evidence.url_http_status}）` : ""}
              </span>
            )}
            {sourceAvailable && evidence.url_health_status === "unchecked" ? (
              <p className="link-warning">尚未自动验证；可打开不代表证据已实质核验。</p>
            ) : evidence.url_checked_at ? (
              <span className="muted">链接检查时间：{formatDate(evidence.url_checked_at)}</span>
            ) : null}
          </div>
        );
      })}
    </article>
  );
}

export default async function CompanyDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ result?: string; error?: string }>;
}) {
  const { id } = await params;
  const { result, error: actionError } = await searchParams;
  try {
    const company = await getCompany(id);
    const [watchlist, usage] = await Promise.all([
      getPersonalWatchlist(),
      getPersonalUsage(),
    ]);
    const isFollowed = watchlist.some((item) => item.company_id === company.id);
    const reportIdempotencyKey = randomBytes(32).toString("hex");
    const materialChanges = company.events.filter(
      (event) => event.display_kind === "confirmed_change" || event.publication_route === "deterministic_change",
    );
    const baselineEvents = company.events.filter(
      (event) => event.display_kind !== "confirmed_change" && event.publication_route !== "deterministic_change",
    );
    const baselineGroups = Object.entries(
      baselineEvents.reduce<Record<string, Event[]>>((groups, event) => {
        (groups[event.event_type] ??= []).push(event);
        return groups;
      }, {}),
    ).sort(([leftType], [rightType]) => {
      const order = Object.keys(eventTypeLabels);
      const leftIndex = order.indexOf(leftType);
      const rightIndex = order.indexOf(rightType);
      return (leftIndex === -1 ? order.length : leftIndex) -
        (rightIndex === -1 ? order.length : rightIndex);
    });
    const feedback = result
      ? result === "followed"
        ? "已加入个人关注。关注仅用于整理，不改变公司或私有数据权限。"
        : result === "unfollowed"
          ? "已取消关注；公司共享档案仍可继续查询。"
          : result === "refresh_requested"
            ? "人工更新申请已进入队列，本次没有触发外部查询。"
            : "相同申请仍在处理或处于 24 小时冷却期，本次没有重复计数。"
      : actionError
        ? actionError === "limit_reached"
          ? "当前测试权益额度已用完。"
          : actionError === "not_available"
            ? "该公司当前不能加入个人关注或申请更新。"
            : "操作失败，请稍后重试。"
        : null;
    return (
      <main className="shell page-stack">
        <Link className="back-link" href="/">
          ← 返回公司查询
        </Link>

        {feedback ? (
          <p className={`feedback ${result ? "feedback-success" : "feedback-error"}`}>
            {feedback}
          </p>
        ) : null}

        <section className="hero detail-hero">
          <div>
            <p className="eyebrow">{company.registered_region ?? "注册地区未知"}</p>
            <h1>{company.legal_name}</h1>
            <p>
              工商主体身份：
              {identityLabel(company.identity_status, company.identity_verification_basis)} · 数据基准日
              {formatDate(company.data_as_of)} · 已发布资料检查：
              {company.last_checked_at ? formatDate(company.last_checked_at) : company.events.length ? "尚未联网检查" : "尚无已发布资料"}
            </p>
            <p>统一社会信用代码：{company.credit_code ?? "暂未收录"}</p>
            {company.official_website ? (
              <a href={company.official_website} rel="noreferrer" target="_blank">
                官方网站 ↗
              </a>
            ) : null}
          </div>
          <div className="personal-detail-controls">
            <span className={`status status-${company.freshness_status}`}>
              {freshnessLabels[company.freshness_status] ?? company.freshness_status}
            </span>
            {company.is_platform_shared ? (
              <>
                <form action={isFollowed ? unfollowCompany : followCompany}>
                  <input name="company_id" type="hidden" value={company.id} />
                  <button className="button button-secondary" type="submit">
                    {isFollowed ? "取消个人关注" : "加入个人关注"}
                  </button>
                </form>
                <form action={requestCompanyRefresh}>
                  <input name="company_id" type="hidden" value={company.id} />
                  <button className="button button-secondary" type="submit">
                    申请人工更新
                  </button>
                </form>
                <form action={generateCompanyReport}>
                  <input name="company_id" type="hidden" value={company.id} />
                  <input
                    name="idempotency_key"
                    type="hidden"
                    value={reportIdempotencyKey}
                  />
                  <button className="button button-secondary" type="submit">
                    生成公司报告
                  </button>
                </form>
                <span className="muted">
                  已关注 {usage.watchlist_companies.used}/{usage.watchlist_companies.limit} ·
                  本月申请 {usage.company_requests.used}/{usage.company_requests.limit} · 本月报告
                  {usage.reports.used}/{usage.reports.limit}
                </span>
              </>
            ) : (
              <span className="muted">机构私有公司不进入个人关注列表</span>
            )}
          </div>
        </section>

        {company.is_platform_shared ? (
          <PersonalChangePanel
            companyId={company.id}
            materialChanges={materialChanges}
            baselineEventIds={baselineEvents.map((event) => event.id)}
          />
        ) : null}

        {company.is_platform_shared ? (
          <section className="panel" aria-label="当前可查看内容">
            <h2>当前可查看内容</h2>
            <p className="section-intro">
              汇总本页当前可见的平台共享内容，包含此前积累和后续补充的记录，不仅限于最近一次查询。
              私有信息另列，不计入这里。
            </p>
            <ul>
              <li>
                <a href="#company-current-changes">已核实变化（含历史）：{materialChanges.length} 条</a>
              </li>
              <li>
                <a href="#company-records">已核实基础资料：{baselineEvents.length} 条</a>
              </li>
              <li>
                {company.platform_unconfirmed_leads.length > 0 ? (
                  <a href="#company-current-leads">待核实线索：{company.platform_unconfirmed_leads.length} 条（不属于已确认事实）</a>
                ) : "待核实线索：0 条"}
              </li>
            </ul>
            {company.events.length === 0 && company.platform_unconfirmed_leads.length === 0 ? (
              <p>当前暂无可展示的共享事实或线索，不代表公司没有重要变化。</p>
            ) : company.events.length === 0 ? (
              <p>目前可查看待核实线索，尚无已核实事实；请结合证据与不确定性阅读。</p>
            ) : null}
          </section>
        ) : null}

        {company.platform_unconfirmed_leads.length > 0 ? (
          <section className="panel attention-panel" id="company-current-leads">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">单独隔离，不与已核实事实混在一起</p>
                <h2>需要留意的待核实线索</h2>
              </div>
              <span className="muted">{company.platform_unconfirmed_leads.length} 条线索</span>
            </div>
            <p className="privacy-note">
              这些条目可能值得留意，但证据尚不足，不代表平台已经确认责任、影响或投资结论。
            </p>
            <details className="lead-list">
              <summary>查看待核实线索</summary>
              <div className="timeline">
                {company.platform_unconfirmed_leads.map((event) => (
                  <EventCard event={event} key={event.id} unconfirmed />
                ))}
              </div>
            </details>
          </section>
        ) : null}

        {company.investments.length > 0 ? (
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">机构私有叠加层</p>
                <h2>投资关系</h2>
              </div>
            </div>
            <div className="investment-grid">
              {company.investments.map((investment) => (
                <InvestmentCard investment={investment} key={investment.fund_id} />
              ))}
            </div>
          </section>
        ) : null}

        {company.private_events.length > 0 ? (
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">机构私有叠加层</p>
                <h2>机构私有已确认信息</h2>
              </div>
              <span className="muted">{company.private_events.length} 条事件</span>
            </div>
            <div className="timeline">
              {company.private_events.map((event) => (
                <EventCard event={event} key={event.id} privateRecord />
              ))}
            </div>
          </section>
        ) : null}

        {company.unconfirmed_leads.length > 0 ? (
          <section className="panel attention-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">当前用户或机构私有</p>
                <h2>私有待核实线索</h2>
              </div>
              <span className="muted">{company.unconfirmed_leads.length} 条线索</span>
            </div>
            <details className="lead-list">
              <summary>查看私有待核实线索</summary>
              <div className="timeline">
                {company.unconfirmed_leads.map((event) => (
                  <EventCard event={event} key={event.id} unconfirmed />
                ))}
              </div>
            </details>
          </section>
        ) : null}

        <section className="panel company-records" id="company-records">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">页面下方，按需查看</p>
              <h2>公司资料与历史记录</h2>
            </div>
            <span className="muted">{baselineEvents.length} 条资料</span>
          </div>
          <p className="section-intro">
            这里保留已经核实的基础资料。默认收起，避免静态记录淹没真正重要的变化。
          </p>

          {baselineEvents.length === 0 ? (
            <div className="empty-state">暂无已核实的当前资料。</div>
          ) : (
            <div className="record-group-list">
              {baselineGroups.map(([eventType, events]) => (
                <details className="record-group" key={eventType}>
                  <summary>
                    <span>{eventTypeLabels[eventType] ?? eventType}</span>
                    <small>{events.length} 条资料</small>
                  </summary>
                  <div className="timeline">
                    {events.map((event) => (
                      <EventCard event={event} key={event.id} />
                    ))}
                  </div>
                </details>
              ))}
            </div>
          )}
        </section>

        <section className="gap-panel">
          <p className="eyebrow">信息缺口</p>
          <p>数据覆盖与检查时间不因缓存重新处理而自动更新；各条证据的检查时间请展开查看。</p>
          <ul>
            {company.information_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>

        {company.personal_research_result ? (
          <section className="panel" aria-label="我的历史查询过程">
            <details>
              <summary>查看我的最近一次查询过程（历史记录）</summary>
              <p className="privacy-note">
                以下仅记录那次任务结束时的情况，不代表当前全部可查看内容。
                后续补充或重新处理的结果请以上方列表为准；任务结束时间不是来源重新检查时间。
              </p>
              <p>当次任务结束时间：{formatDate(company.personal_research_result.finished_at, true)}</p>
              <p>{company.personal_research_result.message}</p>
              <ResearchCoverage items={company.personal_research_result.category_coverage} />
              {company.personal_research_result.limitations.length > 0 ? (
                <ul>
                  {company.personal_research_result.limitations.map((item) => <li key={item}>{item}</li>)}
                </ul>
              ) : null}
            </details>
          </section>
        ) : null}
      </main>
    );
  } catch (error) {
    await redirectIfAuthenticationRequired(error, `/companies/${encodeURIComponent(id)}`);
    return (
      <main className="shell page-stack">
        <Link className="back-link" href="/">
          ← 返回公司查询
        </Link>
        <section className="hero">
          <p className="eyebrow">读取失败</p>
          <h1>无法访问该公司</h1>
          <p>公司不存在、未进入共享目录、当前身份没有私有访问权限，或后端尚未启动。</p>
        </section>
      </main>
    );
  }
}
