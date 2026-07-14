import Link from "next/link";

import { getCompany, type Investment } from "@/lib/api";

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
            <div className="empty-state">暂无已通过人工审核的事件。</div>
          ) : (
            <div className="timeline">
              {company.events.map((event) => (
                <article className="event-card" key={event.id}>
                  <div className="event-meta">
                    <span>{eventTypeLabels[event.event_type] ?? event.event_type}</span>
                    <span>{formatDate(event.occurred_at)}</span>
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
                      <dt>审核状态</dt>
                      <dd>已发布</dd>
                    </div>
                  </dl>
                  {event.evidence.map((evidence) => (
                    <div className="evidence" key={evidence.id}>
                      <div>
                        <span className="eyebrow">证据 · {evidence.source_name}</span>
                        <strong>{evidence.title}</strong>
                      </div>
                      <blockquote>{evidence.excerpt}</blockquote>
                      <a href={evidence.canonical_url} rel="noreferrer" target="_blank">
                        查看虚构来源链接 ↗
                      </a>
                    </div>
                  ))}
                </article>
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
