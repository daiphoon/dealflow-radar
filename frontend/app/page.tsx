import Link from "next/link";

import { requestCompanyInclusion } from "@/app/personal-actions";
import {
  ApiError,
  getCompanies,
  getPersonalUsage,
  searchCompanies,
  type CompanySearchResult,
} from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

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
    let searchLimitReached = false;
    if (query) {
      try {
        searchResults = await searchCompanies(query);
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
        ? "收录申请已进入人工处理队列；系统没有自动创建或绑定公司。"
        : "相同申请仍在处理或处于 24 小时冷却期，本次没有重复计数。"
      : actionError
        ? actionError === "limit_reached"
          ? "本月收录或更新申请额度已用完。"
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
            按工商全称、统一社会信用代码或已核实别名精确查询。查询会直接显示平台已经审核并保存的信息。
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
          <form className="search-form" method="get" role="search">
            <label htmlFor="company-query">公司工商全称、信用代码或已核实别名</label>
            <div>
              <input
                defaultValue={query}
                id="company-query"
                maxLength={240}
                name="q"
                placeholder="请输入准确的工商全称或18位统一社会信用代码"
                required
              />
              <button className="button button-search" type="submit">
                查询
              </button>
            </div>
          </form>

          {query ? (
            <div className="search-results" aria-live="polite">
              <div className="search-summary">
                <strong>{searchLimitReached ? "查询额度已用完" : `${searchResults.length} 条精确结果`}</strong>
                <span className="muted">“{query}”</span>
              </div>
              {searchLimitReached ? (
                <div className="empty-state">
                  本月 {usage.searches.limit} 次测试查询额度已用完；这是一项服务端限制，刷新页面不会绕过。
                </div>
              ) : searchResults.length === 0 ? (
                <div className="empty-state">
                  <p>共享目录中没有精确匹配；本次查询没有自动创建或绑定公司。</p>
                  <form action={requestCompanyInclusion} className="inclusion-request-form">
                    <input name="return_query" type="hidden" value={query} />
                    <label>
                      工商全称（如已知）
                      <input
                        defaultValue={looksLikeCreditCode ? "" : query}
                        maxLength={240}
                        name="company_name"
                        placeholder="请输入准确的工商全称"
                      />
                    </label>
                    <label>
                      统一社会信用代码（如已知）
                      <input
                        defaultValue={looksLikeCreditCode ? query.toUpperCase() : ""}
                        maxLength={32}
                        name="credit_code"
                        placeholder="可留空"
                      />
                    </label>
                    <button className="button button-approve" type="submit">
                      提交人工收录申请
                    </button>
                    <span className="muted">
                      本月申请 {usage.company_requests.used}/{usage.company_requests.limit}
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
