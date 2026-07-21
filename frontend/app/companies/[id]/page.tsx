import { randomBytes } from "node:crypto";

import Link from "next/link";

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
  unknown: "待生成快照",
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
};

function formatDate(value: string | null): string {
  if (!value) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
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
  if (basis === "licensed_business_data") return "已核验（授权工商数据）";
  return "已核验（历史依据未记录）";
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
  const eventDate = event.occurred_at ?? event.published_at ?? event.published_on;
  const publicationLabel = unconfirmed
    ? "未确认线索"
    : privateRecord
      ? "机构私有已确认"
      : event.publication_route === "auto_published"
      ? "规则自动发布"
      : "人工或历史确认";
  return (
    <article className="event-card">
      <div className="event-meta">
        <span>{eventTypeLabels[event.event_type] ?? event.event_type}</span>
        <span>{formatDate(eventDate)}</span>
        <span className={`risk risk-${event.risk_severity}`}>
          {riskLabels[event.risk_severity] ?? event.risk_severity}
        </span>
      </div>
      <h3>{event.title}</h3>
      <p>{event.summary}</p>
      <dl className="score-grid">
        <div>
          <dt>重要性</dt>
          <dd>{event.materiality_score}/100</dd>
        </div>
        <div>
          <dt>可信度</dt>
          <dd>{Math.round(Number(event.confidence_score) * 100)}%</dd>
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
            ? ` 原因：${event.publication_reasons
                .map((reason) => publicationReasonLabels[reason] ?? reason)
                .join("、")}。`
            : ""}
        </p>
      ) : null}
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
            {sourceAvailable ? (
              <a href={sourceUrl} rel="noreferrer" target="_blank">
                查看公开来源链接 ↗
              </a>
            ) : (
              <span className="muted">
                {evidence.url_health_status === "broken"
                  ? "来源链接已失效"
                  : "来源链接不可开放"}
                {evidence.url_http_status ? `（HTTP ${evidence.url_http_status}）` : ""}
              </span>
            )}
            {evidence.url_health_status === "unchecked" ? (
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
              {formatDate(company.data_as_of)} · 最后检查 {formatDate(company.last_checked_at)}
            </p>
            <p>统一社会信用代码：{company.credit_code ?? "Demo 未设置"}</p>
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
                    生成固定报告
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

        {company.is_platform_shared ? <PersonalChangePanel companyId={company.id} /> : null}

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">平台共享基础层</p>
              <h2>已审核事件与可见证据</h2>
            </div>
            <span className="muted">{company.events.length} 条事件</span>
          </div>

          {company.events.length === 0 ? (
            <div className="empty-state">暂无已发布事实。</div>
          ) : (
            <div className="timeline">
              {company.events.map((event) => (
                <EventCard event={event} key={event.id} />
              ))}
            </div>
          )}
        </section>

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
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">当前用户或机构私有</p>
                <h2>未确认线索</h2>
              </div>
              <span className="muted">{company.unconfirmed_leads.length} 条线索</span>
            </div>
            <div className="timeline">
              {company.unconfirmed_leads.map((event) => (
                <EventCard event={event} key={event.id} unconfirmed />
              ))}
            </div>
          </section>
        ) : null}

        <section className="gap-panel">
          <p className="eyebrow">信息缺口</p>
          <ul>
            {company.information_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
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
