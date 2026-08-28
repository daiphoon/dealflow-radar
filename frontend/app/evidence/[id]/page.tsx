import Link from "next/link";

import { getEvidenceDetail } from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

export const dynamic = "force-dynamic";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default async function EvidenceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  try {
    const evidence = await getEvidenceDetail(id);
    return (
      <main className="shell page-stack detail-shell evidence-detail-shell">
        <Link className="back-link" href={`/companies/${evidence.company_id}`}>
          ← 返回公司详情
        </Link>
        <section className="hero compact-hero">
          <div>
            <p className="eyebrow">平台证据详情</p>
            <h1>{evidence.heading}</h1>
            <p>{evidence.company_legal_name}</p>
          </div>
        </section>

        <section className="panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">来源与核验边界</p>
              <h2>{evidence.source_name}</h2>
            </div>
          </div>
          <p>{evidence.description}</p>
          <dl className="metric-list">
            <div>
              <dt>查询时间</dt>
              <dd>{formatDate(evidence.checked_at)}</dd>
            </div>
            <div>
              <dt>来源质量</dt>
              <dd>{evidence.source_quality} 级</dd>
            </div>
            <div>
              <dt>来源记录总数</dt>
              <dd>{evidence.total_records ?? "未提供"}</dd>
            </div>
            <div>
              <dt>本页展示</dt>
              <dd>{evidence.displayed_records} 条</dd>
            </div>
          </dl>
          <p className="privacy-note">
            本页展示授权结构化数据的必要字段，不展示供应商完整响应、联系方式或客户私有资料。
          </p>
          {evidence.total_records !== null &&
          evidence.displayed_records > 0 &&
          evidence.total_records > evidence.displayed_records ? (
            <p className="detail-coverage-note">
              当前来源共返回 {evidence.total_records} 条记录，本页已取得并展示其中 {evidence.displayed_records}
              条；未取得的逐条内容不会由平台推测或补写。
            </p>
          ) : null}
          {evidence.total_records !== null &&
          evidence.total_records > 0 &&
          evidence.displayed_records === 0 ? (
            <p className="detail-coverage-note">
              当前授权接口仅提供分类数量或评分概览，尚未取得可展示的逐条明细。
            </p>
          ) : null}
        </section>

        {evidence.summary_fields.length > 0 ? (
          <section className="panel">
            <div className="section-heading">
              <h2>关键字段</h2>
            </div>
            <dl className="metric-list">
              {evidence.summary_fields.map((field) => (
                <div key={`${field.label}:${field.value}`}>
                  <dt>{field.label}</dt>
                  <dd>{field.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {evidence.records.length > 0 ? (
          <section className="panel">
            <div className="section-heading">
              <div>
                <p className="eyebrow">结构化记录</p>
                <h2>本次可展示明细</h2>
              </div>
            </div>
            <div className="stack-list">
              {evidence.records.map((record, index) => (
                <article className="event-card" key={`${index}:${record.title}`}>
                  <h3>{record.title}</h3>
                  <dl className="metric-list">
                    {record.fields.map((field) => (
                      <div key={`${field.label}:${field.value}`}>
                        <dt>{field.label}</dt>
                        <dd>{field.value}</dd>
                      </div>
                    ))}
                  </dl>
                  {record.source_url ? (
                    <a href={record.source_url} rel="noreferrer" target="_blank">
                      查看该条记录的原始页面 ↗
                    </a>
                  ) : null}
                </article>
              ))}
            </div>
          </section>
        ) : null}

        <section className="panel">
          <div className="section-heading">
            <h2>供应商原始页面</h2>
          </div>
          {evidence.provider_link_available && evidence.provider_url ? (
            <>
              <a href={evidence.provider_url} rel="noreferrer" target="_blank">
                查看供应商原始页面 ↗
              </a>
              {evidence.provider_access_notice ? (
                <p className="link-warning">{evidence.provider_access_notice}</p>
              ) : null}
            </>
          ) : (
            <p className="muted">
              当前供应商响应没有提供可直接定位的企业或记录页面；本页证据详情不受此影响。
            </p>
          )}
        </section>
      </main>
    );
  } catch (error) {
    await redirectIfAuthenticationRequired(error, `/evidence/${encodeURIComponent(id)}`);
    throw error;
  }
}
