import type { CategoryCoverage, ResearchCompletion } from "@/lib/api";

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

const completionLabels: Record<string, string> = {
  complete: "已完成本轮有限检查", completed: "已完成本轮有限检查",
  partial: "部分完成 / 未完成", failed: "研究失败", not_run: "尚未开始 / 未检查",
  budget_deferred: "预算暂缓 / 未完成",
};
const failureLabels: Record<string, string> = {
  source_unreachable: "来源不可达", network_environment_blocked: "环境预检失败",
  robots_denied: "网站读取规则受阻", dynamic_content_unavailable: "正文不可获取",
  budget_deferred: "预算暂缓", not_checked: "未检查",
  access_controlled: "来源要求登录、授权或交互验证",
  rate_limited: "来源限流，本次未完成",
};

export function ResearchCoverage({ items, completion }: {
  items?: CategoryCoverage[]; completion?: ResearchCompletion | null;
}) {
  if (!items?.length && !completion) return null;
  return (
    <section className="research-coverage" aria-label="本次类别来源覆盖">
      {completion ? (
        <section aria-label="本次研究状态与范围">
          <h3>本次研究状态与范围：{completionLabels[completion.status] ?? "检查记录不足"}</h3>
          <p>{completion.scope === "bounded_full_scope_refresh"
            ? "本次计划覆盖八类；预算内执行，未完成类别逐项保留。"
            : "本次只检查有限主题，不能作为全公司检查完成。"}</p>
          {completion.network_preflight_failed ? <p>{completion.status === "not_run"
            ? "环境预检失败，联网研究未开始；已有资料仍可读取。"
            : "环境预检失败，联网研究中断；已完成范围与已产生用量保留。"}</p> : null}
          {completion.status !== "complete" ? <p>本次研究未完成，不能据此判断公司经营正常或没有风险。</p> : null}
          <ul>{completion.categories.map((row) => (
            <li key={row.category}>
              {categoryLabels[row.category] ?? "未分类"}：{completionLabels[row.status] ?? "检查记录不足"}
              {row.failure_class ? `；${failureLabels[row.failure_class] ?? "检查未完成"}` : ""}
              ；最近成功检查：{checkedAt(row.last_successful_check_at)}
            </li>
          ))}</ul>
        </section>
      ) : null}
      {items?.length ? <>
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
      </> : null}
    </section>
  );
}
