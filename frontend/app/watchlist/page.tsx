import Link from "next/link";

import { unfollowCompany } from "@/app/personal-actions";
import {
  getPersonalCompanyRequests,
  getPersonalUsage,
  getPersonalWatchlist,
} from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

export const dynamic = "force-dynamic";

const freshnessLabels: Record<string, string> = {
  fresh: "新鲜",
  stale: "已过期",
  unknown: "待生成快照",
  budget_deferred: "等待可用预算",
};

const requestStatusLabels: Record<string, string> = {
  pending: "待处理",
  in_review: "处理中",
  completed: "已完成",
  rejected: "未受理",
};

function formatDate(value: string | null): string {
  if (!value) return "尚未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default async function WatchlistPage({
  searchParams,
}: {
  searchParams: Promise<{ result?: string; error?: string }>;
}) {
  const { result, error } = await searchParams;
  try {
    const [watchlist, requests, usage] = await Promise.all([
      getPersonalWatchlist(),
      getPersonalCompanyRequests(),
      getPersonalUsage(),
    ]);
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">个人留存闭环</p>
          <h1>我的关注</h1>
          <p>
            这里仅保存当前账户的整理关系。关注不会授予基金权限，也不会让个人看到机构私有数据。
          </p>
        </section>

        {result ? (
          <p className="feedback feedback-success">
            {result === "unfollowed"
              ? "已取消关注。"
              : result === "inclusion_requested"
                ? "收录申请已进入人工处理队列；系统没有自动创建或绑定公司。"
                : "相同申请仍在处理或处于 24 小时冷却期，本次没有重复计数。"}
          </p>
        ) : error ? (
          <p className="feedback feedback-error">操作失败，请稍后重试。</p>
        ) : null}

        <section className="quota-grid" aria-label="当前测试权益">
          <article className="quota-card">
            <span>本月公司查询</span>
            <strong>
              {usage.searches.used} / {usage.searches.limit}
            </strong>
          </article>
          <article className="quota-card">
            <span>个人关注公司</span>
            <strong>
              {usage.watchlist_companies.used} / {usage.watchlist_companies.limit}
            </strong>
          </article>
          <article className="quota-card">
            <span>本月收录或更新申请</span>
            <strong>
              {usage.company_requests.used} / {usage.company_requests.limit}
            </strong>
          </article>
          <article className="quota-card">
            <span>本月固定报告</span>
            <strong>
              {usage.reports.used} / {usage.reports.limit}
            </strong>
            <Link href="/reports">查看我的报告</Link>
          </article>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">个人私有</p>
              <h2>已关注 {watchlist.length} 家公司</h2>
            </div>
            <span className="muted">上限 {usage.watchlist_companies.limit} 家</span>
          </div>
          {watchlist.length === 0 ? (
            <div className="empty-state">
              尚未关注公司。请先从<Link href="/">共享目录</Link>打开公司详情。
            </div>
          ) : (
            <div className="company-grid">
              {watchlist.map((company) => (
                <article className="company-card watchlist-card" key={company.id}>
                  <div className="card-topline">
                    <span className={`status status-${company.freshness_status}`}>
                      {freshnessLabels[company.freshness_status] ?? company.freshness_status}
                    </span>
                    <span className="muted">关注于 {formatDate(company.followed_at)}</span>
                  </div>
                  <Link href={`/companies/${company.company_id}`}>
                    <h3>{company.legal_name}</h3>
                  </Link>
                  <p className="card-event">
                    信用代码：{company.credit_code ?? "未设置"}
                    <br />
                    注册地区：{company.registered_region ?? "未知"}
                  </p>
                  <form action={unfollowCompany} className="card-footer">
                    <input name="company_id" type="hidden" value={company.company_id} />
                    <input name="return_to" type="hidden" value="watchlist" />
                    <Link href={`/companies/${company.company_id}`}>查看公司档案</Link>
                    <button className="text-button" type="submit">
                      取消关注
                    </button>
                  </form>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">明确提交给平台处理</p>
              <h2>我的收录与更新申请</h2>
            </div>
            <span className="muted">共 {requests.length} 条</span>
          </div>
          {requests.length === 0 ? (
            <div className="empty-state">尚未提交申请。</div>
          ) : (
            <div className="request-list">
              {requests.map((request) => (
                <article className="request-card" key={request.id}>
                  <div>
                    <span className="eyebrow">
                      {request.request_type === "inclusion" ? "收录申请" : "更新申请"}
                    </span>
                    <strong>{request.requested_name ?? request.requested_credit_code}</strong>
                  </div>
                  <div>
                    <span className={`review-status review-status-${request.status}`}>
                      {requestStatusLabels[request.status] ?? request.status}
                    </span>
                    <span className="muted">提交于 {formatDate(request.created_at)}</span>
                  </div>
                  {request.decision_reason ? <p>{request.decision_reason}</p> : null}
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
    );
  } catch (caught) {
    await redirectIfAuthenticationRequired(caught, "/watchlist");
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">读取失败</p>
          <h1>暂时无法读取个人关注</h1>
          <p>请确认后端服务和数据库迁移已经完成。</p>
        </section>
      </main>
    );
  }
}
