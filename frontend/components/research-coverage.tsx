import type { CategoryCoverage } from "@/lib/api";

const categoryLabels: Record<string, string> = {
  financial_operation: "财务与经营", financing_cap_table: "融资与股权",
  contract_commercial: "合同与商业进展", product_technology: "产品与技术",
  governance_people: "治理与人员", legal_compliance: "司法与合规",
  capacity_assets: "产能与资产", exit_liquidity: "退出与流动性",
  information_quality: "信息质量",
};
const statusLabels: Record<CategoryCoverage["status"], string> = {
  not_configured: "未配置独立来源", not_checked: "尚未检查", blocked: "读取受阻",
  failed: "检查失败", no_records: "检索成功，未检出记录", candidates_only: "本类正文尚不完整",
  evidence_obtained: "取得相关正文", unknown: "检查记录不足",
};
const routeLabels = {
  business_capital: "财务、融资、合同组合检索",
  technology_risk_exit: "技术、治理、风险、产能、退出组合检索",
};

function checkedAt(value: string | null): string {
  if (!value) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export function ResearchCoverage({ items }: { items?: CategoryCoverage[] }) {
  if (!items?.length) return null;
  return (
    <section className="research-coverage" aria-label="本次类别来源覆盖">
      <h3>本次类别来源覆盖</h3>
      <p className="muted">
        以下是当次有限检索的记录，未检查的类别会单独标明。部分历史任务中，多个类别共用一次组合检索，不代表逐类查全；
        取得正文不等于事实已核实，未检出记录也不代表公司没有风险。
      </p>
      <div className="research-coverage-grid">
        {items.map((item) => (
          <article key={item.category}>
            <div className="research-coverage-heading">
              <strong>{categoryLabels[item.category] ?? "未分类"}</strong>
              <span>{statusLabels[item.status] ?? "检查记录不足"}</span>
            </div>
            {item.route ? <p className="muted">范围：{item.topic ?? routeLabels[item.route]}</p> : null}
            {item.evidence_count !== null ? (
              <p>相关正文 {item.evidence_count} 份 · 受阻 {item.blocked_count} 项 · 失败 {item.failed_count} 项</p>
            ) : null}
            {item.gaps.length > 0 ? <ul>{item.gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul> : null}
            {item.route ? (
              <details>
                <summary>查看检查时间{item.cache_reused ? "（含缓存复用）" : ""}</summary>
                <p>本轮尝试：{checkedAt(item.last_attempt_at)}</p>
                <p>搜索结果基准时间：{checkedAt(item.search_checked_at)}</p>
                <p>最近正文成功检查：{checkedAt(item.evidence_checked_at)}</p>
                {item.cache_reused ? <p className="muted">复用缓存不会刷新原来源检查时间。</p> : null}
              </details>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
