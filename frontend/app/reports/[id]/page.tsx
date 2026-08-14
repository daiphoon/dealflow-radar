import Link from "next/link";

import { ReportContent } from "@/components/report-content";
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
              ? "这份报告已存在，已直接为你打开；不会重复占用本月次数。"
              : "报告已生成，可在“我的报告”中再次查看。"}
          </p>
        ) : null}

        <section className="hero report-hero">
          <div>
            <p className="eyebrow">仅自己可见 · 历史报告</p>
            <h1>{report.title}</h1>
            <p>
              公司：{report.company_legal_name} · 生成于 {formatDateTime(report.as_of)}
            </p>
          </div>
          <div className="report-meta">
            <span>{report.source_event_count} 条已审核事件</span>
          </div>
        </section>

        <section className="panel report-notice">
          <strong>阅读提示</strong>
          <p>
            这份报告记录生成时已经审核的信息。此后如有新增、纠正或撤回，请以公司最新详情为准。
          </p>
        </section>

        <section className="panel" aria-labelledby="report-content-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">结构化公司报告</p>
              <h2 id="report-content-title">报告正文</h2>
            </div>
            <span className="muted">内容来自已审核资料</span>
          </div>
          <ReportContent markdown={report.markdown} />
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
          <p>报告不存在、不属于当前账户，或系统暂时不可用。</p>
        </section>
      </main>
    );
  }
}
