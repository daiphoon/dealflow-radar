import Link from "next/link";

import { requestCompanyInclusion } from "@/app/personal-actions";
import { CompanySearchForm } from "@/components/company-search-form";
import {
  ApiError,
  getCompanies,
  getCompanySuggestions,
  getPersonalUsage,
  searchCompanies,
  type CompanySearchResult,
  type CompanySuggestion,
} from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

export const dynamic = "force-dynamic";

const freshnessLabels: Record<string, string> = {
  fresh: "新鲜",
  stale: "已过期",
  refreshing: "后台更新中",
  unknown: "检查状态未确认",
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

export default async function CompanyListPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; result?: string; error?: string }>;
}) {
  const { q = "", result, error: actionError } = await searchParams;
  const query = q.trim();
  try {
    const companies = await getCompanies();
    let searchResults: CompanySearchResult[] = [];
    let suggestionResults: CompanySuggestion[] = [];
    let searchLimitReached = false;
    if (query) {
      try {
        if ([...query].length >= 2) {
          suggestionResults = await getCompanySuggestions(query);
        }
        searchResults = await searchCompanies(query);
        if (searchResults.length > 0) suggestionResults = [];
      } catch (error) {
        if (error instanceof ApiError && error.status === 429) {
          searchLimitReached = true;
        } else {
          throw error;
        }
      }
    }
    const usage = await getPersonalUsage();
    const looksLikeCreditCode = /^[0-9A-Z]{18}$/i.test(query);
    const feedback = result
      ? result === "inclusion_requested"
        ? "申请已提交，可在“我的关注与查询”中查看处理状态。"
        : "相同申请仍在处理或处于 24 小时冷却期，本次没有重复计数。"
      : actionError
        ? actionError === "limit_reached"
          ? "今日或本月研究申请额度已用完；如有实际需要，可申请临时提高额度。"
          : actionError === "already_available"
            ? "该公司已经在共享目录中，请直接精确查询。"
            : "申请未提交，请检查输入后重试。"
        : null;
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">未上市公司 · 平台共享档案</p>
          <h1>直接查询公司</h1>
          <p>
            可以输入常用简称查看候选，再通过工商全称、信用代码和注册地区确认正确公司。
            查询会直接显示平台已经审核并保存的信息。
          </p>
        </section>

        {feedback ? (
          <p className={`feedback ${result ? "feedback-success" : "feedback-error"}`}>
            {feedback}
          </p>
        ) : null}

        <section className="panel" aria-labelledby="company-search-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">个人查询路径</p>
              <h2 id="company-search-title">搜索平台共享目录</h2>
            </div>
            <span className="muted">
              本月查询 {usage.searches.used}/{usage.searches.limit}
            </span>
          </div>
          <CompanySearchForm
            defaultValue={query}
            suggestionsEnabled={usage.searches.remaining > 0}
          />

          {query ? (
            <div className="search-results" aria-live="polite">
              <div className="search-summary">
                <strong>
                  {searchLimitReached
                    ? "查询额度已用完"
                    : searchResults.length > 0
                      ? `${searchResults.length} 条精确结果`
                      : suggestionResults.length > 0
                        ? `${suggestionResults.length} 个可能匹配`
                        : "没有找到匹配公司"}
                </strong>
                <span className="muted">“{query}”</span>
              </div>
              {searchLimitReached ? (
                <div className="empty-state">
                  本月 {usage.searches.limit} 次测试查询额度已用完；这是一项服务端限制，刷新页面不会绕过。
                </div>
              ) : searchResults.length === 0 && suggestionResults.length > 0 ? (
                <div className="suggestion-results-panel">
                  <p>没有完全相同的名称，请从以下已核验公司中选择：</p>
                  <div className="company-suggestion-grid">
                    {suggestionResults.map((company) => (
                      <Link
                        className="company-suggestion-card"
                        href={`/companies/${company.id}`}
                        key={company.id}
                      >
                        <strong>{company.legal_name}</strong>
                        <span>{company.registered_region ?? "注册地区未知"}</span>
                        <span>信用代码：{company.credit_code ?? "暂未收录"}</span>
                      </Link>
                    ))}
                  </div>
                  <p className="muted">请选择与工商信息一致的公司；系统不会根据简称自动绑定。</p>
                </div>
              ) : searchResults.length === 0 ? (
                <div className="empty-state">
                  <p>
                    共享目录中没有找到匹配公司。系统不会根据简称猜测外部公司，请输入准确工商全称或统一社会信用代码。
                  </p>
                  <form action={requestCompanyInclusion} className="inclusion-request-form">
                    <input name="return_query" type="hidden" value={query} />
                    <label>
                      准确工商全称
                      <input
                        defaultValue={looksLikeCreditCode ? "" : query}
                        maxLength={240}
                        name="company_name"
                        placeholder="请输入准确的工商全称"
                      />
                    </label>
                    <label>
                      统一社会信用代码（推荐）
                      <input
                        defaultValue={looksLikeCreditCode ? query.toUpperCase() : ""}
                        maxLength={18}
                        minLength={18}
                        name="credit_code"
                        pattern="[0-9A-Za-z]{18}"
                        placeholder="18 位代码；填写后定位最准确"
                      />
                    </label>
                    <button className="button button-approve" type="submit">
                      提交公司收录与研究申请
                    </button>
                    <span className="muted">
                      今日 {usage.daily_company_requests.used}/
                      {usage.daily_company_requests.limit} · 本月 {usage.company_requests.used}/
                      {usage.company_requests.limit}
                    </span>
                  </form>
                </div>
              ) : (
                <div className="company-grid">
                  {searchResults.map((company) => (
                    <Link
                      className="company-card search-result-card"
                      href={`/companies/${company.id}`}
                      key={company.id}
                    >
                      <div className="card-topline">
                        <span className={`status status-${company.freshness_status}`}>
                          {freshnessLabels[company.freshness_status] ?? company.freshness_status}
                        </span>
                        <span className="muted">身份已核验</span>
                      </div>
                      <h3>{company.legal_name}</h3>
                      <p className="card-event">
                        信用代码：{company.credit_code ?? "暂未收录"}
                        <br />
                        注册地区：{company.registered_region ?? "未知"}
                      </p>
                      <div className="card-footer">
                        <span>打开共享公司档案</span>
                        <span>最后检查：{formatDate(company.last_checked_at)}</span>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </section>

        <section className="panel" aria-labelledby="company-list-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">机构私有路径</p>
              <h2 id="company-list-title">已获基金授权的 {companies.length} 家公司</h2>
            </div>
            <span className="muted">投资数据仍按基金权限过滤</span>
          </div>

          {companies.length === 0 ? (
            <div className="empty-state">当前测试身份没有基金授权，仍可使用上方共享目录查询。</div>
          ) : (
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
                  <p className="card-event">
                    {company.latest_event_title ?? "等待已授权事实或线索"}
                  </p>
                  <div className="card-footer">
                    <span>
                      身份：{company.identity_status === "verified" ? "已核验" : "待核验"}
                    </span>
                    <span>最后检查：{formatDate(company.last_checked_at)}</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>
    );
  } catch (error) {
    await redirectIfAuthenticationRequired(
      error,
      query ? `/?q=${encodeURIComponent(query)}` : "/",
    );
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">服务状态</p>
          <h1>暂时无法读取公司数据</h1>
          <p>系统暂时无法读取公司信息，请稍后再试。</p>
        </section>
      </main>
    );
  }
}
