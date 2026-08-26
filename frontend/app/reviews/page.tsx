import Link from "next/link";

import {
  ApiError,
  getPlatformQuotaIncreaseRequests,
  getReviewWorkbench,
  getSharingCandidates,
  type PersonalQuotaIncreaseRequest,
  type ReviewWorkbenchItem,
  type SharingCandidate,
} from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

import {
  submitIdentityResolution,
  submitReviewDecision,
  submitQuotaIncreaseDecision,
  submitSharingPromotion,
  submitSharingRejection,
  submitSharingRetraction,
} from "./actions";

export const dynamic = "force-dynamic";

const eventTypeLabels: Record<string, string> = {
  financial_operation: "财务与经营",
  financing_cap_table: "融资与股权",
  contract_commercial: "合同与商业化",
  product_technology: "产品与技术",
  governance_people: "治理与人员",
  legal_compliance: "司法与合规",
  capacity_assets: "产能与资产",
  exit_liquidity: "退出与流动性",
  information_quality: "信息质量",
};

const directionLabels: Record<string, string> = {
  positive: "积极",
  negative: "消极",
  neutral: "中性",
  mixed: "混合",
  unknown: "未知",
};

const riskLabels: Record<string, string> = {
  none: "无显著风险",
  low: "低风险",
  moderate: "中等风险",
  high: "高风险",
  critical: "严重风险",
};

const triggerLabels: Record<string, string> = {
  manual_research_import: "人工研究导入",
  human_review_required: "强制人工审核",
  identity_unresolved: "主体待解析",
  existing_event_new_evidence: "已有事件的新证据",
};

const resultMessages: Record<string, string> = {
  approve: "已批准候选事件并更新发布状态。",
  reject: "已驳回候选事件，审核理由已保留。",
  identity_resolved: "已确认工商主体，原始记录已按现行策略自动重新路由。",
  sharing_promoted: "已生成独立的平台共享事实，原私有候选与底稿保持不变。",
  sharing_rejected: "已拒绝本次共享晋升，决定理由已记录。",
  sharing_retracted: "已撤回平台共享事实，个人路径不再展示。",
  quota_approved: "已批准临时研究额度；额度只在设定有效期内生效。",
  quota_rejected: "已拒绝临时研究额度申请，理由已保存。",
};

const errorMessages: Record<string, string> = {
  invalid_input: "请填写 3—1000 字的决定理由。",
  invalid_identity_input: "请选择一条官方工商候选、填写理由并确认操作。",
  identity_forbidden: "候选核验已过期、与该线索无关，或当前身份无权修改主数据。",
  forbidden: "该审核项不可决定、已被处理，或当前身份无权操作。",
  request_failed: "决定提交失败，数据未变更，请检查后端状态。",
  invalid_sharing_input: "请完整填写共享表述、理由，选择证据并确认证据支持。",
  sharing_ineligible: "该候选未满足晋升条件：请检查主体、风险、证据、链接和许可状态。",
  sharing_failed: "共享事实决定提交失败，原私有数据未变更。",
  invalid_quota_decision: "请检查批准额度、有效天数和决定理由。",
  quota_decision_failed: "临时额度决定提交失败，原申请没有变更。",
};

const quotaStatusLabels: Record<PersonalQuotaIncreaseRequest["status"], string> = {
  pending: "待处理",
  approved: "已批准",
  rejected: "未批准",
  expired: "已过期",
};

function QuotaRequestCard({ request }: { request: PersonalQuotaIncreaseRequest }) {
  return (
    <article className="request-card">
      <div>
        <span className="eyebrow">{request.owner_display_name}</span>
        <strong>{request.owner_email}</strong>
        <span className="muted">
          申请日额度 +{request.requested_daily_extra}、月额度 +
          {request.requested_monthly_extra}
        </span>
      </div>
      <div>
        <span className={`review-status review-status-${request.status}`}>
          {quotaStatusLabels[request.status]}
        </span>
        <span className="muted">提交于 {formatDateTime(request.created_at)}</span>
      </div>
      <p>申请理由：{request.request_reason}</p>
      {request.status === "pending" ? (
        <form action={submitQuotaIncreaseDecision} className="quota-decision-form">
          <input name="request_id" type="hidden" value={request.id} />
          <label>
            批准每日增加
            <input
              defaultValue={request.requested_daily_extra}
              max="100"
              min="0"
              name="approved_daily_extra"
              type="number"
            />
          </label>
          <label>
            批准每月增加
            <input
              defaultValue={request.requested_monthly_extra}
              max="1000"
              min="0"
              name="approved_monthly_extra"
              type="number"
            />
          </label>
          <label>
            有效天数
            <input defaultValue="7" max="90" min="1" name="valid_days" type="number" />
          </label>
          <label className="wide-field">
            决定理由
            <textarea maxLength={1000} minLength={3} name="reason" required rows={2} />
          </label>
          <div className="review-actions wide-field">
            <button className="button button-approve" name="status" type="submit" value="approved">
              批准临时额度
            </button>
            <button className="button button-reject" name="status" type="submit" value="rejected">
              拒绝申请
            </button>
          </div>
        </form>
      ) : null}
    </article>
  );
}

function formatDateTime(value: string | null): string {
  if (!value) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatSourceDate(publishedAt: string | null, publishedOn: string | null): string {
  if (publishedAt) return formatDateTime(publishedAt);
  if (!publishedOn) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeZone: "Asia/Shanghai",
  }).format(new Date(publishedOn));
}

function statusLabel(status: string): string {
  if (status === "pending") return "待审";
  if (status === "approved") return "已批准";
  if (status === "rejected") return "已驳回";
  return status;
}

function publicationRouteLabel(route: string): string {
  if (route === "auto_published") return "规则自动发布";
  if (route === "unconfirmed_lead") return "未确认线索";
  if (route === "human_confirmed" || route === "legacy_reviewed") return "人工或历史确认";
  return route;
}

function identityBasisLabel(basis: string): string {
  if (basis === "official_government") return "政府官方来源";
  if (basis === "licensed_business_data") return "授权工商数据";
  return "未知核验依据";
}

function ReviewCard({ review }: { review: ReviewWorkbenchItem }) {
  const event = review.event;
  return (
    <article className="review-card">
      <div className="review-card-heading">
        <div>
          <p className="eyebrow">
            {review.company_legal_name ?? review.mention_text ?? "主体待确认"}
          </p>
          <h3>{event?.title ?? "主体身份待解析"}</h3>
        </div>
        <span className={`review-status review-status-${review.status}`}>
          {statusLabel(review.status)}
        </span>
      </div>

      <div className="trigger-list" aria-label="触发规则">
        {review.trigger_rules.map((rule) => (
          <span key={rule}>{triggerLabels[rule] ?? rule}</span>
        ))}
      </div>

      {event ? (
        <>
          <p className="review-summary">{event.summary}</p>
          <p className="privacy-note">
            当前发布路由：{publicationRouteLabel(event.publication_route)}。
            {review.status === "approved" && event.publication_route === "unconfirmed_lead"
              ? " 历史批准记录仍保留，但该事件因当前质量规则已降级，不进入公司快照。"
              : ""}
          </p>
          <div className="event-meta review-time-grid">
            <span>事件时间：{formatDateTime(event.occurred_at)}</span>
            <span>来源发布：{formatSourceDate(event.published_at, event.published_on)}</span>
            <span>系统发现：{formatDateTime(event.observed_at)}</span>
          </div>

          <dl className="score-grid review-score-grid">
            <div>
              <dt>类别</dt>
              <dd>{eventTypeLabels[event.event_type] ?? event.event_type}</dd>
            </div>
            <div>
              <dt>方向</dt>
              <dd>{directionLabels[event.direction] ?? event.direction}</dd>
            </div>
            <div>
              <dt>重要性</dt>
              <dd>{event.materiality_score}/100</dd>
            </div>
            <div>
              <dt>风险</dt>
              <dd>{riskLabels[event.risk_severity] ?? event.risk_severity}</dd>
            </div>
            <div>
              <dt>可信度 / 来源</dt>
              <dd>
                {Math.round(Number(event.confidence_score) * 100)}% / {event.source_quality} 级
              </dd>
            </div>
          </dl>

          <div className="review-detail-grid">
            <section>
              <h4>结构化事实</h4>
              <dl className="fact-list">
                {event.facts.map((fact, index) => (
                  <div key={`${fact.name}-${index}`}>
                    <dt>{fact.name}</dt>
                    <dd>
                      {fact.value}
                      {fact.unit ? ` ${fact.unit}` : ""}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
            <section>
              <h4>不确定性</h4>
              {event.uncertainties.length ? (
                <ul>
                  {event.uncertainties.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">暂无额外不确定性记录。</p>
              )}
            </section>
          </div>

          <div className="review-evidence-list">
            {event.evidence.map((evidence) => {
              const sourceAvailable = evidence.link_display_allowed;
              return (
                <div className="evidence" key={evidence.id}>
                  <div>
                    <span className="eyebrow">
                      证据 · {evidence.source_name} · {evidence.source_quality} 级
                    </span>
                    <strong>{evidence.title}</strong>
                  </div>
                  <blockquote>{evidence.excerpt}</blockquote>
                  <div className="evidence-footer">
                    <span>系统发现：{formatDateTime(evidence.observed_at)}</span>
                    {sourceAvailable ? (
                      <a
                        href={evidence.final_url ?? evidence.canonical_url}
                        rel="noreferrer"
                        target="_blank"
                      >
                        查看公开来源 ↗
                      </a>
                    ) : (
                      <span className="muted">
                        {evidence.url_health_status === "broken"
                          ? "来源链接已失效"
                          : "来源链接不可开放"}
                        {evidence.url_http_status ? `（HTTP ${evidence.url_http_status}）` : ""}
                      </span>
                    )}
                  </div>
                  {evidence.url_health_status === "unchecked" ? (
                    <p className="link-warning">尚未自动验证；可打开不代表证据已实质核验。</p>
                  ) : evidence.url_checked_at ? (
                    <p className="muted">链接检查时间：{formatDateTime(evidence.url_checked_at)}</p>
                  ) : null}
                </div>
              );
            })}
          </div>

          {review.status === "pending" ? (
            <form action={submitReviewDecision} className="review-form">
              <input name="review_id" type="hidden" value={review.id} />
              <label htmlFor={`reason-${review.id}`}>
                决定理由（必填）
              </label>
              <textarea
                id={`reason-${review.id}`}
                maxLength={1000}
                minLength={3}
                name="reason"
                placeholder="说明主体、事实、证据与不确定性的核验结论"
                required
                rows={3}
              />
              <label className="review-confirmation">
                <input name="confirmed" required type="checkbox" />
                <span>我已核对主体、证据、时间和不确定性，并理解本次决定会写入数据库。</span>
              </label>
              <div className="review-actions">
                <button className="button button-approve" name="decision" type="submit" value="approve">
                  批准并发布
                </button>
                <button className="button button-reject" name="decision" type="submit" value="reject">
                  驳回候选
                </button>
              </div>
              <p>批准后将在事务中发布事件并重建快照；页面不会调用外部 Provider。</p>
            </form>
          ) : (
            <div className="decision-note">
              <strong>审核结果：{statusLabel(review.status)}</strong>
              <span>{review.decision_reason ?? "未记录理由"}</span>
              <span>{formatDateTime(review.decided_at)}</span>
            </div>
          )}
        </>
      ) : (
        <div className="identity-review-note">
          <p>
            匹配规则：{review.match_rule ?? "未知"} · 匹配置信度：
            {review.match_confidence ? `${Math.round(Number(review.match_confidence) * 100)}%` : "未知"}
          </p>
          <p>
            本条资料公司归属：{review.resolution_status ?? "unresolved"}。只有存在身份歧义的工商候选需要人工选择；选定后系统会自动重建事件并按现行策略路由。
          </p>
          {review.status === "pending" && review.identity_candidates.length ? (
            <form action={submitIdentityResolution} className="review-form">
              <input name="review_id" type="hidden" value={review.id} />
              <fieldset>
                <legend>选择工商主体</legend>
                {review.identity_candidates.map((candidate) => (
                  <label className="review-confirmation" key={candidate.verification_id}>
                    <input
                      name="verification_id"
                      required
                      type="radio"
                      value={candidate.verification_id}
                    />
                    <span>
                      <strong>{candidate.legal_name}</strong>
                      <br />
                      统一社会信用代码：{candidate.credit_code} · 注册地：
                      {candidate.registered_region ?? "未载明"} · 登记状态：
                      {candidate.registration_status}
                      <br />
                      核验依据：{identityBasisLabel(candidate.verification_basis)} ·
                      <a href={candidate.canonical_url} rel="noreferrer" target="_blank">
                        {candidate.source_name} ↗
                      </a>
                      · 核验时间：{formatDateTime(candidate.checked_at)}
                    </span>
                  </label>
                ))}
              </fieldset>
              <label htmlFor={`identity-reason-${review.id}`}>选择理由（必填）</label>
              <textarea
                id={`identity-reason-${review.id}`}
                maxLength={1000}
                minLength={3}
                name="reason"
                placeholder="说明代码、工商全称和注册地的对应结论"
                required
                rows={3}
              />
              <label className="review-confirmation">
                <input name="confirmed" required type="checkbox" />
                <span>我已核对所示来源、信用代码和工商全称，并理解选择会更新公司身份主数据。</span>
              </label>
              <button className="button button-approve" type="submit">
                确认主体并重新路由
              </button>
              <p>该操作只读取已入库证据，不会在页面请求中访问外部网站。</p>
            </form>
          ) : review.status === "pending" ? (
            <p className="muted">
              暂无在当前有效期内且与该线索关联的工商身份候选。请先通过受控身份导入命令入库。
            </p>
          ) : (
            <div className="decision-note">
              <strong>身份处理结果：{statusLabel(review.status)}</strong>
              <span>{review.decision_reason ?? "未记录理由"}</span>
              <span>{formatDateTime(review.decided_at)}</span>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function SharingCandidateCard({ candidate }: { candidate: SharingCandidate }) {
  const event = candidate.event;
  const hasUncheckedLink = event.evidence.some(
    (evidence) => evidence.url_health_status === "unchecked",
  );
  const latestDecision = candidate.decisions.at(-1);
  const displayState =
    candidate.shared_event_status === "published"
      ? "shared"
      : candidate.shared_event_status === "retracted"
        ? "retracted"
        : latestDecision?.action === "reject"
          ? "rejected"
          : "pending";
  const displayStateLabel = {
    shared: "已共享",
    retracted: "已撤回",
    rejected: "已拒绝",
    pending: "待决定",
  }[displayState];
  return (
    <article className="review-card">
      <div className="review-card-heading">
        <div>
          <p className="eyebrow">
            {candidate.company_legal_name} · {candidate.company_credit_code ?? "信用代码缺失"}
          </p>
          <h3>{event.title}</h3>
        </div>
        <span
          className={`review-status review-status-${
            displayState === "shared"
              ? "approved"
              : displayState === "pending"
                ? "pending"
                : "rejected"
          }`}
        >
          {displayStateLabel}
        </span>
      </div>
      <p className="privacy-note">
        原作用域：{candidate.source_scope} · 来源所有者：{candidate.owner_name}
        。晋升后原候选与原文仍归原所有者。
      </p>
      {candidate.identity_ambiguous ? (
        <div className="feedback feedback-error">主体存在歧义，当前不允许晋升。</div>
      ) : null}
      <p className="review-summary">{event.summary}</p>
      <dl className="score-grid review-score-grid">
        <div>
          <dt>类别</dt>
          <dd>{eventTypeLabels[event.event_type] ?? event.event_type}</dd>
        </div>
        <div>
          <dt>方向</dt>
          <dd>{directionLabels[event.direction] ?? event.direction}</dd>
        </div>
        <div>
          <dt>重要性</dt>
          <dd>{event.materiality_score}/100</dd>
        </div>
        <div>
          <dt>风险</dt>
          <dd>{riskLabels[event.risk_severity] ?? event.risk_severity}</dd>
        </div>
        <div>
          <dt>可见证据</dt>
          <dd>{event.evidence.length} 条</dd>
        </div>
      </dl>

      <div className="review-evidence-list">
        {event.evidence.map((evidence) => (
          <div className="evidence" key={evidence.id}>
            <div>
              <span className="eyebrow">
                证据 · {evidence.source_name} · {evidence.source_quality} 级
              </span>
              <strong>{evidence.title}</strong>
            </div>
            <blockquote>{evidence.excerpt}</blockquote>
            <div className="evidence-footer">
              <span>来源日期：{formatSourceDate(evidence.published_at, evidence.published_on)}</span>
              {evidence.link_display_allowed ? (
                <a
                  href={evidence.final_url ?? evidence.canonical_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  查看来源 ↗
                </a>
              ) : (
                <span className="muted">
                  {evidence.url_health_status === "broken"
                    ? "来源链接已失效"
                    : "来源链接不可开放"}
                </span>
              )}
            </div>
            {evidence.url_health_status === "unchecked" ? (
              <p className="link-warning">尚未自动验证；可打开不代表证据已实质核验。</p>
            ) : evidence.url_checked_at ? (
              <p className="muted">链接检查时间：{formatDateTime(evidence.url_checked_at)}</p>
            ) : null}
          </div>
        ))}
      </div>

      {displayState === "shared" && candidate.shared_event_id ? (
        <form action={submitSharingRetraction} className="review-form">
          <input name="shared_event_id" type="hidden" value={candidate.shared_event_id} />
          <label htmlFor={`retract-reason-${candidate.source_event_id}`}>撤回理由</label>
          <textarea
            id={`retract-reason-${candidate.source_event_id}`}
            maxLength={1000}
            minLength={3}
            name="reason"
            required
            rows={3}
          />
          <button className="button button-reject" type="submit">
            撤回共享事实
          </button>
          <p>撤回只停止个人路径展示，不删除原候选、证据或审计记录。</p>
        </form>
      ) : displayState === "pending" ? (
        <>
          <form action={submitSharingPromotion} className="review-form">
            <input name="source_event_id" type="hidden" value={candidate.source_event_id} />
            <label htmlFor={`sharing-title-${candidate.source_event_id}`}>共享事实标题</label>
            <textarea
              defaultValue={event.title}
              id={`sharing-title-${candidate.source_event_id}`}
              maxLength={200}
              minLength={3}
              name="title"
              required
              rows={2}
            />
            <label htmlFor={`sharing-summary-${candidate.source_event_id}`}>谨慎共享表述</label>
            <textarea
              defaultValue={event.summary}
              id={`sharing-summary-${candidate.source_event_id}`}
              maxLength={2000}
              minLength={3}
              name="summary"
              required
              rows={4}
            />
            <fieldset>
              <legend>选择允许展示的证据引用</legend>
              {event.evidence.map((evidence) => (
                <label className="review-confirmation" key={evidence.id}>
                  <input defaultChecked name="evidence_ids" type="checkbox" value={evidence.id} />
                  <span>
                    {evidence.source_name}：{evidence.title}（{evidence.url_health_status}）
                  </span>
                </label>
              ))}
            </fieldset>
            <label htmlFor={`sharing-reason-${candidate.source_event_id}`}>晋升理由</label>
            <textarea
              id={`sharing-reason-${candidate.source_event_id}`}
              maxLength={1000}
              minLength={3}
              name="reason"
              required
              rows={3}
            />
            <label className="review-confirmation">
              <input name="confirm_evidence_support" required type="checkbox" />
              <span>我已确认共享表述受所选证据支持。</span>
            </label>
            {hasUncheckedLink ? (
              <label className="review-confirmation">
                <input name="confirm_unchecked_links" required type="checkbox" />
                <span>我已人工打开并检查未自动验证的链接。</span>
              </label>
            ) : null}
            <button className="button button-approve" type="submit">
              生成独立共享事实
            </button>
            <p>操作不会改变原私有事件和原始文档的作用域。</p>
          </form>
          <form action={submitSharingRejection} className="review-form">
            <input name="source_event_id" type="hidden" value={candidate.source_event_id} />
            <label htmlFor={`reject-sharing-${candidate.source_event_id}`}>拒绝共享理由</label>
            <textarea
              id={`reject-sharing-${candidate.source_event_id}`}
              maxLength={1000}
              minLength={3}
              name="reason"
              required
              rows={3}
            />
            <button className="button button-reject" type="submit">
              拒绝本次共享晋升
            </button>
          </form>
        </>
      ) : (
        <p className="privacy-note">
          {displayState === "retracted"
            ? "该共享事实已停止对个人路径展示；原私有候选和审计记录继续保留。"
            : "该私有候选已被拒绝晋升；原记录继续保持私有。"}
        </p>
      )}
      {latestDecision ? (
        <div className="decision-note">
          <strong>最近决定：{latestDecision.action}</strong>
          <span>{latestDecision.reason}</span>
          <span>{formatDateTime(latestDecision.created_at)}</span>
        </div>
      ) : null}
    </article>
  );
}

export default async function ReviewsPage({
  searchParams,
}: {
  searchParams: Promise<{ result?: string; error?: string }>;
}) {
  const query = await searchParams;
  try {
    const reviews = await getReviewWorkbench();
    let sharingCandidates: SharingCandidate[] = [];
    let quotaRequests: PersonalQuotaIncreaseRequest[] = [];
    try {
      sharingCandidates = await getSharingCandidates();
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 403)) throw error;
    }
    try {
      quotaRequests = await getPlatformQuotaIncreaseRequests();
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 403)) throw error;
    }
    const sortedReviews = [...reviews].sort((left, right) => {
      const statusDifference = Number(left.status !== "pending") - Number(right.status !== "pending");
      return statusDifference || left.created_at.localeCompare(right.created_at);
    });
    const pendingCount = reviews.filter((review) => review.status === "pending").length;
    return (
      <main className="shell page-stack">
        <section className="hero detail-hero">
          <div>
            <p className="eyebrow">本机受控验证</p>
            <h1>身份例外与共享事实审核</h1>
            <p>
              平台管理员可把合格的私有候选生成独立共享事实；原底稿不共享。打开页面不会产生外部调用。
            </p>
          </div>
          <Link className="back-link" href="/">
            返回公司列表 →
          </Link>
        </section>

        {query.result && resultMessages[query.result] ? (
          <div className="feedback feedback-success">{resultMessages[query.result]}</div>
        ) : null}
        {query.error && errorMessages[query.error] ? (
          <div className="feedback feedback-error">{errorMessages[query.error]}</div>
        ) : null}

        {quotaRequests.length ? (
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">平台管理员专用</p>
                <h2>临时研究额度申请</h2>
              </div>
              <span className="muted">
                {quotaRequests.filter((request) => request.status === "pending").length} 条待处理
              </span>
            </div>
            <div className="request-list">
              {quotaRequests.map((request) => (
                <QuotaRequestCard key={request.id} request={request} />
              ))}
            </div>
          </section>
        ) : null}

        {sharingCandidates.length ? (
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">平台管理员专用</p>
                <h2>受控平台共享事实晋升</h2>
              </div>
              <span className="muted">{sharingCandidates.length} 条私有候选</span>
            </div>
            <div className="review-list">
              {sharingCandidates.map((candidate) => (
                <SharingCandidateCard candidate={candidate} key={candidate.source_event_id} />
              ))}
            </div>
          </section>
        ) : null}

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">待处理与历史</p>
              <h2>{pendingCount} 个待处理项</h2>
            </div>
            <span className="muted">共 {reviews.length} 条审核记录</span>
          </div>

          {sortedReviews.length ? (
            <div className="review-list">
              {sortedReviews.map((review) => (
                <ReviewCard key={review.id} review={review} />
              ))}
            </div>
          ) : (
            <div className="empty-state">当前没有需要处理的审核项。</div>
          )}
        </section>

        <section className="gap-panel review-security-note">
          <p className="eyebrow">安全边界</p>
          <p>
            本页面默认关闭，仅可在受控环境显式开启。CloudBase 只核验登录身份，审核权限仍由本平台角色和租户边界强制执行。
          </p>
        </section>
      </main>
    );
  } catch (error) {
    await redirectIfAuthenticationRequired(error, "/reviews");
    const disabled = error instanceof ApiError && error.status === 404;
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">{disabled ? "安全开关" : "读取失败"}</p>
          <h1>{disabled ? "审核工作台未开启" : "暂时无法读取审核队列"}</h1>
          <p>
            {disabled
              ? "仅在本机受控环境设置 REVIEW_WORKBENCH_ENABLED=true 后使用。"
              : "请检查后端、数据库与审核员角色配置。"}
          </p>
        </section>
      </main>
    );
  }
}
