import Link from "next/link";

import { getCompanies } from "@/lib/api";

export const dynamic = "force-dynamic";

const freshnessLabels: Record<string, string> = {
  fresh: "新鲜",
  stale: "已过期",
  refreshing: "后台更新中",
  unknown: "待生成快照",
};

const riskLabels: Record<string, string> = {
  none: "无显著风险",
  low: "低风险",
  moderate: "中等风险",
  high: "高风险",
  critical: "严重风险",
};

function formatDate(value: string | null): string {
  if (!value) return "尚未检查";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default async function CompanyListPage() {
  try {
    const companies = await getCompanies();
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">投后监测 · 最近一次已发布结果</p>
          <h1>公司组合</h1>
          <p>
            页面只读取数据库，不会因访问而调用搜索或大模型。只展示当前测试身份已获基金授权的公司。
          </p>
        </section>

        <section className="panel" aria-labelledby="company-list-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">已授权范围</p>
              <h2 id="company-list-title">{companies.length} 家公司</h2>
            </div>
            <span className="muted">按基金权限过滤</span>
          </div>

          <div className="company-grid">
            {companies.map((company) => (
              <Link className="company-card" href={`/companies/${company.id}`} key={company.id}>
                <div className="card-topline">
                  <span className={`status status-${company.freshness_status}`}>
                    {freshnessLabels[company.freshness_status] ?? company.freshness_status}
                  </span>
                  <span className={`risk risk-${company.highest_risk ?? "unknown"}`}>
                    {company.highest_risk
                      ? riskLabels[company.highest_risk] ?? company.highest_risk
                      : "暂无已发布事件"}
                  </span>
                </div>
                <h3>{company.legal_name}</h3>
                <p className="card-event">{company.latest_event_title ?? "等待人工审核后发布事件"}</p>
                <div className="card-footer">
                  <span>身份：{company.identity_status === "verified" ? "已核验" : "待核验"}</span>
                  <span>最后检查：{formatDate(company.last_checked_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        </section>
      </main>
    );
  } catch {
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">服务状态</p>
          <h1>暂时无法读取公司数据</h1>
          <p>请确认后端已启动、数据库迁移及 Demo 种子导入已完成。</p>
        </section>
      </main>
    );
  }
}
