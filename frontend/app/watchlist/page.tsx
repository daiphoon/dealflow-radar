import Link from "next/link";

import {
  cancelCompanyRequest,
  requestTemporaryQuotaIncrease,
  unfollowCompany,
} from "@/app/personal-actions";
import {
  getPersonalCompanyRequests,
  getPersonalQuotaIncreaseRequests,
  getPersonalUsage,
  getPersonalWatchlist,
} from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

import { RequestStatusRefresher } from "./request-status-refresher";

export const dynamic = "force-dynamic";

const freshnessLabels: Record<string, string> = {
  fresh: "新鲜",
  stale: "已过期",
  unknown: "待生成快照",
  budget_deferred: "等待可用预算",
};

const requestStatusLabels: Record<string, string> = {
  pending: "待人工处理",
  in_review: "人工处理中",
  identity_queued: "身份核验排队中",
  identity_checking: "正在核验身份",
  awaiting_confirmation: "等待你确认公司",
  needs_input: "需要准确信用代码",
  research_queued: "研究排队中",
  researching: "后台研究中",
  partial: "已有部分结果",
  budget_deferred: "额度暂缓",
  cancel_requested: "正在安全取消",
  cancelled: "已取消",
  completed: "已完成",
  rejected: "未受理",
  failed: "处理失败",
};

const activeRequestStatuses = new Set([
  "identity_queued",
  "identity_checking",
  "research_queued",
  "researching",
  "partial",
  "budget_deferred",
  "cancel_requested",
]);

const researchModuleLabels: Record<string, string> = {
  company_base: "工商与股东基础",
  risk: "司法与合规风险",
  intellectual_property: "知识产权",
  operation: "经营与公示",
  history: "历史变更",
  executive: "董监高与人员",
};

const researchModuleStatusLabels: Record<string, string> = {
  pending: "等待检查",
  running: "正在检查",
  completed: "已取得资料",
  no_data: "暂无可靠公开数据",
  failed: "本次检查未完成",
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
    const [watchlist, requests, usage, quotaRequests] = await Promise.all([
      getPersonalWatchlist(),
      getPersonalCompanyRequests(),
      getPersonalUsage(),
      getPersonalQuotaIncreaseRequests(),
    ]);
    const hasActiveRequest = requests.some((request) => activeRequestStatuses.has(request.status));
    const hasPendingQuotaRequest = quotaRequests.some((request) => request.status === "pending");
    return (
      <main className="shell page-stack">
        <RequestStatusRefresher active={hasActiveRequest} />
        <section className="hero">
          <p className="eyebrow">个人留存闭环</p>
          <h1>我的关注与查询</h1>
          <p>
            这里仅保存当前账户的整理关系。关注不会授予基金权限，也不会让个人看到机构私有数据。
          </p>
        </section>

        {result ? (
          <p className="feedback feedback-success">
            {result === "unfollowed"
              ? "已取消关注。"
              : result === "request_cancelled"
                  ? "已取消申请，平台不会开始新的处理步骤。"
                  : result === "quota_requested"
                    ? "临时额度申请已提交，等待平台管理员处理。"
                    : result === "inclusion_requested"
                      ? "申请已提交；请在本页查看工商核验和后续处理状态。"
                      : "相同申请仍在处理或处于 24 小时冷却期，本次没有重复计数。"}
          </p>
        ) : error ? (
          <p className="feedback feedback-error">
            {error === "invalid_quota_request"
              ? "请填写需要增加的额度和至少 5 个字的申请理由。"
              : "操作失败，请检查当前状态后重试。"}
          </p>
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
            <span>今日新公司研究申请</span>
            <strong>
              {usage.daily_company_requests.used} / {usage.daily_company_requests.limit}
            </strong>
          </article>
          <article className="quota-card">
            <span>本月新公司研究申请</span>
            <strong>
              {usage.company_requests.used} / {usage.company_requests.limit}
            </strong>
          </article>
          <article className="quota-card">
            <span>本月公司报告</span>
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
              <h2>我的公司研究申请</h2>
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
                      {request.request_type === "inclusion" ? "新公司研究" : "已有公司更新"}
                    </span>
                    <strong>{request.requested_name ?? request.requested_credit_code}</strong>
                    {request.requested_credit_code ? (
                      <span className="muted">信用代码：{request.requested_credit_code}</span>
                    ) : null}
                  </div>
                  <div>
                    <span className={`review-status review-status-${request.status}`}>
                      {requestStatusLabels[request.status] ?? request.status}
                    </span>
                    <span className="muted">提交于 {formatDate(request.created_at)}</span>
                  </div>
                  <p>{request.status_message}</p>
                  {Object.keys(request.research_modules).length > 0 ? (
                    <ul className="research-module-list" aria-label="研究模块进度">
                      {Object.entries(request.research_modules).map(([module, status]) => (
                        <li key={module}>
                          <span>{researchModuleLabels[module] ?? module}</span>
                          <strong>{researchModuleStatusLabels[status] ?? status}</strong>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {request.queue_position ? (
                    <p>当前可见队列位置：第 {request.queue_position} 位。</p>
                  ) : null}
                  {request.resolved_legal_name ? (
                    <section className="request-identity-card" aria-label="待确认工商主体">
                      <h3>工商主体核验结果</h3>
                      <dl>
                        <div>
                          <dt>工商全称</dt>
                          <dd>{request.resolved_legal_name}</dd>
                        </div>
                        <div>
                          <dt>统一社会信用代码</dt>
                          <dd>{request.resolved_credit_code ?? "未返回"}</dd>
                        </div>
                        <div>
                          <dt>注册地区</dt>
                          <dd>{request.resolved_registered_region ?? "未返回"}</dd>
                        </div>
                        <div>
                          <dt>登记状态</dt>
                          <dd>{request.resolved_registration_status ?? "未返回"}</dd>
                        </div>
                      </dl>
                    </section>
                  ) : null}
                  <div className="request-card-footer">
                    <span className="muted">最近更新 {formatDate(request.updated_at)}</span>
                    {request.company_id ? (
                      <Link href={`/companies/${request.company_id}`}>查看公司档案</Link>
                    ) : null}
                  </div>
                  {request.can_cancel ? (
                    <form action={cancelCompanyRequest} className="request-cancel-form">
                      <input name="request_id" type="hidden" value={request.id} />
                      <input
                        maxLength={500}
                        name="reason"
                        placeholder="取消原因（可选，例如：公司输入错误）"
                      />
                      <button className="button button-secondary" type="submit">
                        取消查询
                      </button>
                    </form>
                  ) : null}
                  {request.cancellation_reason ? (
                    <p>取消原因：{request.cancellation_reason}</p>
                  ) : request.decision_reason ? (
                    <p>{request.decision_reason}</p>
                  ) : null}
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">防止恶意消耗并支持真实需要</p>
              <h2>临时增加研究额度</h2>
            </div>
            <span className="muted">由平台管理员人工决定，有效期到期后自动失效</span>
          </div>
          {hasPendingQuotaRequest ? (
            <div className="empty-state">已有一项额度申请等待处理，请勿重复提交。</div>
          ) : (
            <form action={requestTemporaryQuotaIncrease} className="quota-request-form">
              <label>
                每日额外增加
                <input defaultValue="0" max="100" min="0" name="requested_daily_extra" type="number" />
              </label>
              <label>
                每月额外增加
                <input defaultValue="0" max="1000" min="0" name="requested_monthly_extra" type="number" />
              </label>
              <label className="wide-field">
                申请理由
                <textarea
                  maxLength={500}
                  minLength={5}
                  name="reason"
                  placeholder="请说明需要临时查询更多公司的原因"
                  required
                  rows={2}
                />
              </label>
              <div className="wide-field">
                <button className="button button-secondary" type="submit">
                  提交额度申请
                </button>
              </div>
            </form>
          )}
          {quotaRequests.length ? (
            <div className="quota-request-history">
              {quotaRequests.map((quotaRequest) => (
                <p key={quotaRequest.id}>
                  {formatDate(quotaRequest.created_at)}：申请日额度 +
                  {quotaRequest.requested_daily_extra}、月额度 +
                  {quotaRequest.requested_monthly_extra}；状态：
                  {quotaRequest.status === "pending"
                    ? "待处理"
                    : quotaRequest.status === "approved"
                      ? `已批准（有效至 ${formatDate(quotaRequest.effective_until)}）`
                      : quotaRequest.status === "rejected"
                        ? "未批准"
                        : "已过期"}
                </p>
              ))}
            </div>
          ) : null}
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
