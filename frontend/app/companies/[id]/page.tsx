import Link from "next/link";

import { getCompany, type Event, type Investment } from "@/lib/api";

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

function EventCard({ event, unconfirmed = false }: { event: Event; unconfirmed?: boolean }) {
  const eventDate = event.occurred_at ?? event.published_at ?? event.published_on;
  const publicationLabel = unconfirmed
    ? "未确认线索"
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
        const sourceAvailable = evidence.url_health_status === "healthy";
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
                来源链接{evidence.url_health_status === "unchecked" ? "尚未检查" : "当前不可用"}
                {evidence.url_http_status ? `（HTTP ${evidence.url_http_status}）` : ""}
              </span>
            )}
          </div>
        );
      })}
    </article>
  );
}

export default async function CompanyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  try {
    const company = await getCompany(id);
    return (
      <main className="shell page-stack">
        <Link className="back-link" href="/">
          ← 返回公司列表
        </Link>

        <section className="hero detail-hero">
          <div>
            <p className="eyebrow">{company.registered_region ?? "注册地区未知"}</p>
            <h1>{company.legal_name}</h1>
            <p>
              身份{company.identity_status === "verified" ? "已核验" : "待核验"} · 数据基准日
              {formatDate(company.data_as_of)} · 最后检查 {formatDate(company.last_checked_at)}
            </p>
            {company.official_website ? (
              <a href={company.official_website} rel="noreferrer" target="_blank">
                官方网站 ↗
              </a>
            ) : null}
          </div>
          <span className={`status status-${company.freshness_status}`}>
            {freshnessLabels[company.freshness_status] ?? company.freshness_status}
          </span>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">权限隔离</p>
              <h2>投资关系</h2>
            </div>
          </div>
          <div className="investment-grid">
            {company.investments.map((investment) => (
              <InvestmentCard investment={investment} key={investment.fund_id} />
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">已发布事实</p>
              <h2>事件与证据</h2>
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

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">程序自动保留</p>
              <h2>未确认线索</h2>
            </div>
            <span className="muted">{company.unconfirmed_leads.length} 条线索</span>
          </div>
          {company.unconfirmed_leads.length === 0 ? (
            <div className="empty-state">暂无未确认线索。</div>
          ) : (
            <div className="timeline">
              {company.unconfirmed_leads.map((event) => (
                <EventCard event={event} key={event.id} unconfirmed />
              ))}
            </div>
          )}
        </section>

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
  } catch {
    return (
      <main className="shell page-stack">
        <Link className="back-link" href="/">
          ← 返回公司列表
        </Link>
        <section className="hero">
          <p className="eyebrow">读取失败</p>
          <h1>无法访问该公司</h1>
          <p>公司不存在、当前测试身份没有基金权限，或后端服务尚未启动。</p>
        </section>
      </main>
    );
  }
}
