import Link from "next/link";

import { getPersonalCompanyReport } from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

export const dynamic = "force-dynamic";

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "long",
    timeStyle: "medium",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default async function PersonalReportDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ result?: string }>;
}) {
  const { id } = await params;
  const { result } = await searchParams;
  try {
    const report = await getPersonalCompanyReport(id);
    return (
      <main className="shell page-stack">
        <div className="back-links">
          <Link className="back-link" href="/reports">
            ← 返回我的报告
          </Link>
          <Link className="back-link" href={`/companies/${report.company_id}`}>
            返回公司详情
          </Link>
        </div>

        {result ? (
          <p className="feedback feedback-success">
            {result === "report_reused"
              ? "相同提交已复用原报告，本月额度没有重复计数。"
              : "固定模板报告已生成；本次没有调用大模型或外部数据源。"}
          </p>
        ) : null}

        <section className="hero report-hero">
          <div>
            <p className="eyebrow">个人私有 · 只读时点快照</p>
            <h1>{report.title}</h1>
            <p>
              公司：{report.company_legal_name} · 生成于 {formatDateTime(report.as_of)}
            </p>
          </div>
          <div className="report-meta">
            <span>{report.source_event_count} 条共享事件</span>
            <span>模板 {report.report_version}</span>
            <span>校验 {report.content_hash.slice(0, 12)}…</span>
          </div>
        </section>

        <section className="panel report-notice">
          <strong>阅读提示</strong>
          <p>
            这是生成时点的固定报告。后续新增、纠正或撤回不会改写这份历史快照，请以公司最新详情为准。
          </p>
        </section>

        <section className="panel" aria-labelledby="report-markdown-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">确定性 Markdown</p>
              <h2 id="report-markdown-title">报告正文</h2>
            </div>
            <span className="muted">不含 LLM 生成内容</span>
          </div>
          <pre className="report-markdown">{report.markdown}</pre>
        </section>
      </main>
    );
  } catch (error) {
    await redirectIfAuthenticationRequired(error, `/reports/${encodeURIComponent(id)}`);
    return (
      <main className="shell page-stack">
        <Link className="back-link" href="/reports">
          ← 返回我的报告
        </Link>
        <section className="hero">
          <p className="eyebrow">读取失败</p>
          <h1>无法访问该报告</h1>
          <p>报告不存在、属于其他用户，或后端尚未启动。</p>
        </section>
      </main>
    );
  }
}
