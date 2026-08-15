import Link from "next/link";

import { getPersonalCompanyReports, getPersonalUsage } from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

export const dynamic = "force-dynamic";

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default async function PersonalReportsPage() {
  try {
    const [reports, usage] = await Promise.all([
      getPersonalCompanyReports(),
      getPersonalUsage(),
    ]);
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">个人私有</p>
          <h1>我的公司报告</h1>
          <p>
            报告只使用生成时已经审核、允许展示的信息，不包含基金投资数据、机构资料或未确认线索。
          </p>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">当前测试权益</p>
              <h2>本月已生成 {usage.reports.used} 份</h2>
            </div>
            <span className="muted">
              上限 {usage.reports.limit} · 剩余 {usage.reports.remaining}
            </span>
          </div>
          {reports.length === 0 ? (
            <div className="empty-state">
              尚未生成报告。请从公司详情页选择“生成公司报告”。
            </div>
          ) : (
            <div className="report-list">
              {reports.map((report) => (
                <article className="report-list-item" key={report.id}>
                  <div>
                    <p className="eyebrow">{formatDateTime(report.created_at)}</p>
                    <h3>{report.title}</h3>
                    <p className="muted">
                      {report.source_event_count} 条已审核事件
                    </p>
                  </div>
                  <div className="report-list-actions">
                    <Link href={`/reports/${report.id}`}>查看报告</Link>
                    <Link href={`/companies/${report.company_id}`}>公司详情</Link>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
    );
  } catch (error) {
    await redirectIfAuthenticationRequired(error, "/reports");
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">读取失败</p>
          <h1>暂时无法读取个人报告</h1>
          <p>请确认后端服务和当前账户状态。</p>
        </section>
      </main>
    );
  }
}
