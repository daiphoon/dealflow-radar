import Link from "next/link";

import {
  ApiError,
  getCandidateDocuments,
  getCompanies,
  getTrustedSourceRuns,
  getTrustedSources,
  type CandidateDocument,
  type SourceCheckRun,
  type TrustedSource,
} from "@/lib/api";
import { redirectIfAuthenticationRequired } from "@/lib/auth-navigation";

import {
  submitCandidateDecision,
  submitCandidateResearch,
  submitSourceEnabled,
  submitSourceListPathPrefix,
  submitSourcePolicy,
  submitSourceRun,
  submitTrustedSource,
} from "./actions";

export const dynamic = "force-dynamic";

const resultMessages: Record<string, string> = {
  source_created: "可信来源已登记；尚未发起任何网络请求。",
  source_enabled: "来源已启用。",
  source_disabled: "来源已停用，排队任务不会访问该来源。",
  source_scope_updated: "列表页内容路径已更新；后续检查只访问该路径下的页面。",
  source_policy_updated: "来源许可与保留策略已更新；历史候选和底稿未被改写。",
  dry_run_queued: "Dry-run 已入队；Worker 处理时不会发起网络请求。",
  real_run_queued: "受控检查已入队；只有两个外部访问开关均开启时 Worker 才会联网。",
  candidate_decided: "候选处理决定已保存；没有自动生成事件或共享事实。",
  candidate_research_imported:
    "候选已转为机构私有研究底稿与候选事件；没有自动共享或发布。",
};

const errorMessages: Record<string, string> = {
  invalid_input: "输入不符合安全约束，请检查 HTTPS 地址、根域名、许可依据和频率。",
  invalid_decision: "请填写 3—1000 字的判断理由并选择有效操作。",
  invalid_research: "研究结构化字段不完整或不符合取值范围，未创建底稿。",
  run_confirmation_required: "真实检查需要勾选一次性外部访问确认。",
  forbidden: "当前身份不是本租户平台管理员，无法访问运营候选。",
  conflict: "该来源或运行已存在，未创建重复记录。",
  request_failed: "请求失败，数据未变更；请检查后端、数据库和 Worker 状态。",
};

const sourceTypeLabels: Record<string, string> = {
  single_page: "单页",
  list_page: "列表页",
  rss: "RSS / Atom",
  sitemap: "Sitemap",
};

const candidateStatusLabels: Record<string, string> = {
  pending: "待人工处理",
  worth_research: "值得研究",
  irrelevant: "无关",
  duplicate: "重复",
  source_unavailable: "来源失效",
};

function formatDateTime(value: string | null): string {
  if (!value) return "尚无记录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function SourceCard({ source }: { source: TrustedSource }) {
  return (
    <article className="monitor-card">
      <div className="monitor-heading">
        <div>
          <p className="eyebrow">
            {source.company_legal_name} · {sourceTypeLabels[source.source_type] ?? source.source_type}
          </p>
          <h3>{source.name}</h3>
        </div>
        <span className={`review-status review-status-${source.enabled ? "approved" : "rejected"}`}>
          {source.enabled ? "已启用" : "已停用"}
        </span>
      </div>
      <dl className="monitor-metadata">
        <div>
          <dt>起始地址</dt>
          <dd className="break-text">{source.start_url}</dd>
        </div>
        <div>
          <dt>允许域名</dt>
          <dd>{source.root_domain}</dd>
        </div>
        <div>
          <dt>列表内容路径</dt>
          <dd>{source.list_path_prefix ?? "自动推断（建议实样验证后明确设置）"}</dd>
        </div>
        <div>
          <dt>访问依据 / 许可</dt>
          <dd>
            {source.access_basis} / {source.license_status}
          </dd>
        </div>
        <div>
          <dt>保留策略</dt>
          <dd>{source.content_retention_policy}</dd>
        </div>
        <div>
          <dt>最近成功</dt>
          <dd>{formatDateTime(source.last_success_at)}</dd>
        </div>
        <div>
          <dt>失败状态</dt>
          <dd>
            {source.last_failure_code ?? "无"}（连续 {source.consecutive_failures} 次）
          </dd>
        </div>
      </dl>
      <details className="monitor-details">
        <summary>更新许可与保留策略</summary>
        <form action={submitSourcePolicy} className="source-config-form compact-config-form">
          <input name="source_id" type="hidden" value={source.id} />
          <label className="wide-field">
            访问许可或使用依据
            <textarea
              defaultValue={source.access_basis}
              maxLength={2000}
              minLength={3}
              name="access_basis"
              required
              rows={2}
            />
          </label>
          <label>
            许可状态
            <select defaultValue={source.license_status} name="license_status" required>
              <option value="public_access">公开访问（仅可作私有研究）</option>
              <option value="permission_confirmed">已确认允许共享引用</option>
              <option value="unclear">许可待确认</option>
              <option value="restricted">访问或使用受限</option>
            </select>
          </label>
          <label>
            内容保留
            <select
              defaultValue={source.content_retention_policy}
              name="content_retention_policy"
              required
            >
              <option value="metadata_only">仅元数据</option>
              <option value="minimal_excerpt">许可范围内最小摘录</option>
            </select>
          </label>
          <div className="wide-field">
            <button className="button button-secondary" type="submit">保存许可策略</button>
          </div>
        </form>
      </details>
      <form action={submitSourceRun} className="monitor-run-form">
        <input name="source_id" type="hidden" value={source.id} />
        <label className="review-confirmation">
          <input name="confirmed" type="checkbox" />
          <span>仅本次确认允许 Worker 发起免费的受控 HTTP 请求；开关仍需另行开启。</span>
        </label>
        <div className="review-actions">
          <button className="button button-secondary" name="run_mode" type="submit" value="dry_run">
            排队 Dry-run
          </button>
          <button
            className="button button-approve"
            disabled={!source.enabled}
            name="run_mode"
            type="submit"
            value="real"
          >
            排队真实检查
          </button>
        </div>
      </form>
      {source.source_type === "list_page" ? (
        <form action={submitSourceListPathPrefix} className="inline-form source-scope-form">
          <input name="source_id" type="hidden" value={source.id} />
          <label>
            内容路径前缀
            <input
              defaultValue={source.list_path_prefix ?? ""}
              maxLength={500}
              name="list_path_prefix"
              placeholder="/Companynews"
            />
          </label>
          <button className="text-button" type="submit">保存路径范围</button>
        </form>
      ) : null}
      <form action={submitSourceEnabled} className="inline-form">
        <input name="source_id" type="hidden" value={source.id} />
        <input name="enabled" type="hidden" value={source.enabled ? "false" : "true"} />
        <button className="text-button" type="submit">
          {source.enabled ? "停用来源" : "重新启用"}
        </button>
      </form>
    </article>
  );
}

function RunCard({ run }: { run: SourceCheckRun }) {
  return (
    <article className="monitor-run-card">
      <div className="monitor-heading">
        <strong>{run.source_name}</strong>
        <span className="muted">
          {run.dry_run ? "Dry-run" : "受控检查"} · {run.trigger_type === "scheduled" ? "定时入队" : "人工入队"} · {run.status}
        </span>
      </div>
      <div className="run-counts">
        <span>请求 {run.request_count}</span>
        <span>下载 {run.downloaded_bytes.toLocaleString("zh-CN")} B</span>
        <span>新增 {run.new_count}</span>
        <span>变化 {run.changed_count}</span>
        <span>无变化 {run.unchanged_count}</span>
        <span>重复 {run.duplicate_count}</span>
        <span>失败 {run.failure_count}</span>
      </div>
      <p className="privacy-note">
        robots：{run.robots_status ?? "未检查"}；外部调用 {run.external_calls}；付费调用 {run.paid_api_calls}；
        Token {run.input_tokens + run.output_tokens}；费用 ¥{run.estimated_cost}。
      </p>
      {run.error_code ? <p className="link-warning">{run.error_code}：{run.error_message}</p> : null}
      {run.scheduled_for ? (
        <span className="muted">应检查时间：{formatDateTime(run.scheduled_for)}</span>
      ) : null}
      <span className="muted">{formatDateTime(run.finished_at ?? run.created_at)}</span>
    </article>
  );
}

function CandidateCard({ candidate }: { candidate: CandidateDocument }) {
  const sourceCanOpen =
    candidate.canonical_url.startsWith("https://") &&
    candidate.link_health_status !== "unsafe" &&
    candidate.license_status !== "restricted";
  const researchImported = candidate.handoff_payload.research_import_completed === true;
  const researchImportAllowed =
    candidate.identity_status_at_discovery === "verified" &&
    candidate.link_health_status !== "broken" &&
    (candidate.current_source_license_status === "public_access" ||
      candidate.current_source_license_status === "permission_confirmed");
  return (
    <article className="monitor-card">
      <div className="monitor-heading">
        <div>
          <p className="eyebrow">
            {candidate.company_legal_name} · {candidate.source_name}
          </p>
          <h3>{candidate.title}</h3>
        </div>
        <span className={`review-status review-status-${candidate.processing_status === "pending" ? "pending" : "approved"}`}>
          {candidateStatusLabels[candidate.processing_status] ?? candidate.processing_status}
        </span>
      </div>
      <div className="run-counts">
        <span>{candidate.change_type === "changed" ? "内容变化" : "新页面"}</span>
        <span>链接：{candidate.link_health_status}</span>
        <span>HTTP {candidate.http_status ?? "未知"}</span>
        <span>当前许可：{candidate.current_source_license_status}</span>
        {candidate.license_status !== candidate.current_source_license_status ? (
          <span>发现时许可：{candidate.license_status}</span>
        ) : null}
        <span>发现：{formatDateTime(candidate.first_discovered_at)}</span>
      </div>
      {candidate.excerpt ? <blockquote className="candidate-excerpt">{candidate.excerpt}</blockquote> : null}
      {sourceCanOpen ? (
        <a className="source-link" href={candidate.canonical_url} rel="noreferrer" target="_blank">
          打开已登记来源 ↗
        </a>
      ) : (
        <p className="link-warning">当前链接不得直接开放；保留来源元数据供审计。</p>
      )}
      {candidate.processing_status === "pending" ? (
        <form action={submitCandidateDecision} className="review-form">
          <input name="candidate_id" type="hidden" value={candidate.id} />
          <label htmlFor={`candidate-reason-${candidate.id}`}>人工判断理由</label>
          <textarea
            id={`candidate-reason-${candidate.id}`}
            maxLength={1000}
            minLength={3}
            name="reason"
            required
            rows={2}
          />
          <div className="candidate-actions">
            <button className="button button-approve" name="decision" type="submit" value="worth_research">
              交给现有研究流程
            </button>
            <button className="button button-secondary" name="decision" type="submit" value="irrelevant">
              标记无关
            </button>
            <button className="button button-secondary" name="decision" type="submit" value="duplicate">
              标记重复
            </button>
            <button className="button button-reject" name="decision" type="submit" value="source_unavailable">
              标记来源失效
            </button>
          </div>
          <p>先保存候选判断；标记“值得研究”后再人工填写结构化事实，不会自动发布。</p>
        </form>
      ) : (
        <>
          <div className="decision-note">
            <strong>{candidateStatusLabels[candidate.processing_status]}</strong>
            <span>{candidate.decision_reason}</span>
            <span>{formatDateTime(candidate.processed_at)}</span>
            {researchImported ? (
              <span>
                已建立私有研究底稿与候选事件 {String(candidate.handoff_payload.private_event_id)}；
                需要共享时仍须前往事实审核工作台人工晋升。
              </span>
            ) : null}
          </div>
          {candidate.processing_status === "worth_research" && !researchImported ? (
            researchImportAllowed ? (
              <details className="monitor-details candidate-research-details">
                <summary>录入谨慎事实候选</summary>
                <form
                  action={submitCandidateResearch}
                  className="review-form source-config-form candidate-research-form"
                >
                  <input name="candidate_id" type="hidden" value={candidate.id} />
                  <label className="wide-field">
                    谨慎标题
                    <input defaultValue={candidate.title} maxLength={200} minLength={3} name="title" required />
                  </label>
                  <label className="wide-field">
                    证据最小摘录
                    <textarea
                      defaultValue={candidate.excerpt ?? ""}
                      maxLength={1000}
                      minLength={3}
                      name="evidence_excerpt"
                      required
                      rows={3}
                    />
                  </label>
                  <label>
                    事件类型
                    <select defaultValue="product_technology" name="event_type" required>
                      <option value="financial_operation">财务经营</option>
                      <option value="financing_cap_table">融资与股权</option>
                      <option value="contract_commercial">合同与商业</option>
                      <option value="product_technology">产品与技术</option>
                      <option value="governance_people">治理与人员</option>
                      <option value="legal_compliance">司法与合规</option>
                      <option value="capacity_assets">产能与资产</option>
                      <option value="exit_liquidity">退出与流动性</option>
                      <option value="information_quality">信息质量</option>
                    </select>
                  </label>
                  <label>
                    事件子类型（英文 snake_case）
                    <input defaultValue="company_update" maxLength={64} minLength={2} name="event_subtype" pattern="[a-z][a-z0-9_]*" required />
                  </label>
                  <label>
                    方向
                    <select defaultValue="neutral" name="direction" required>
                      <option value="positive">正向</option>
                      <option value="negative">负向</option>
                      <option value="neutral">中性</option>
                      <option value="mixed">混合</option>
                      <option value="unknown">未知</option>
                    </select>
                  </label>
                  <label>
                    重要性（0—100）
                    <input defaultValue="50" max="100" min="0" name="materiality_score" required type="number" />
                  </label>
                  <label>
                    风险级别
                    <select defaultValue="low" name="risk_severity" required>
                      <option value="none">无</option>
                      <option value="low">低</option>
                      <option value="moderate">中</option>
                      <option value="high">高</option>
                      <option value="critical">严重</option>
                    </select>
                  </label>
                  <label>
                    置信度（0—1）
                    <input defaultValue="0.8" max="1" min="0" name="confidence_score" required step="0.01" type="number" />
                  </label>
                  <label>
                    来源质量
                    <select defaultValue="B" name="source_quality" required>
                      <option value="A">A</option>
                      <option value="B">B</option>
                      <option value="C">C</option>
                      <option value="D">D</option>
                    </select>
                  </label>
                  <label>
                    事实字段（英文 snake_case）
                    <input defaultValue="update_summary" maxLength={80} minLength={2} name="fact_name" pattern="[a-z][a-z0-9_]*" required />
                  </label>
                  <label className="wide-field">
                    事实值
                    <input defaultValue={candidate.title} maxLength={500} name="fact_value" required />
                  </label>
                  <label>
                    单位（可选）
                    <input maxLength={40} name="fact_unit" />
                  </label>
                  <label>
                    事件发生日期（可选）
                    <input name="occurred_on" type="date" />
                  </label>
                  <label className="wide-field">
                    不确定性（可选，每行一条）
                    <textarea maxLength={10000} name="uncertainties" rows={2} />
                  </label>
                  <label className="wide-field">
                    研究判断理由
                    <textarea maxLength={1000} minLength={3} name="research_reason" required rows={2} />
                  </label>
                  <div className="wide-field">
                    <button className="button button-approve" type="submit">创建私有研究候选</button>
                  </div>
                  <p className="wide-field">
                    此操作仅建立本机构私有底稿、证据和候选事件；不自动共享、晋升或发布。
                    {candidate.current_source_license_status === "public_access"
                      ? " 当前仅确认网页可公开访问，不允许后续共享晋升。"
                      : " 后续如需共享，仍须单独人工审核。"}
                  </p>
                </form>
              </details>
            ) : (
              <p className="link-warning">公司身份、链接或来源许可不满足交接条件，不能创建研究底稿。
              </p>
            )
          ) : null}
        </>
      )}
    </article>
  );
}

export default async function MonitoringPage({
  searchParams,
}: {
  searchParams: Promise<{ result?: string; error?: string }>;
}) {
  const query = await searchParams;
  try {
    const [companies, sources, runs, candidates] = await Promise.all([
      getCompanies(),
      getTrustedSources(),
      getTrustedSourceRuns(),
      getCandidateDocuments(),
    ]);
    return (
      <main className="shell page-stack">
        <section className="hero detail-hero">
          <div>
            <p className="eyebrow">平台运营 · 受控来源</p>
            <h1>可信来源监测与候选队列</h1>
            <p>
              管理员登记来源并把检查任务交给后台 Worker。页面读取数据库，不会联网；候选默认私有且不会自动发布。
            </p>
          </div>
          <Link className="back-link" href="/reviews">
            前往事实审核 →
          </Link>
        </section>

        {query.result && resultMessages[query.result] ? (
          <div className="feedback feedback-success">{resultMessages[query.result]}</div>
        ) : null}
        {query.error && errorMessages[query.error] ? (
          <div className="feedback feedback-error">{errorMessages[query.error]}</div>
        ) : null}

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">明确登记，不自动发现域名</p>
              <h2>新增可信来源</h2>
            </div>
            <span className="muted">HTTPS · 低频 · 免费公开访问</span>
          </div>
          {companies.length ? (
            <form action={submitTrustedSource} className="source-config-form">
              <label>
                公司
                <select name="company_id" required>
                  {companies.map((company) => (
                    <option key={company.id} value={company.id}>
                      {company.legal_name}（{company.identity_status}）
                    </option>
                  ))}
                </select>
              </label>
              <label>
                来源名称
                <input maxLength={200} minLength={2} name="name" required />
              </label>
              <label>
                抓取方式
                <select name="source_type" required>
                  <option value="single_page">单页</option>
                  <option value="list_page">列表页</option>
                  <option value="rss">RSS / Atom</option>
                  <option value="sitemap">Sitemap</option>
                </select>
              </label>
              <label>
                根域名
                <input name="root_domain" placeholder="example.com" required />
              </label>
              <label className="wide-field">
                起始 URL
                <input name="start_url" placeholder="https://example.com/news" required type="url" />
              </label>
              <label className="wide-field">
                列表内容路径前缀（仅列表页，可选）
                <input name="list_path_prefix" placeholder="/news/detail" />
              </label>
              <label className="wide-field">
                访问许可或使用依据
                <textarea maxLength={2000} minLength={3} name="access_basis" required rows={2} />
              </label>
              <label>
                许可状态
                <select name="license_status" required>
                  <option value="public_access">公开访问（不代表再分发许可）</option>
                  <option value="permission_confirmed">已确认许可</option>
                  <option value="unclear">许可待确认</option>
                  <option value="restricted">访问或使用受限</option>
                </select>
              </label>
              <label>
                检查间隔（分钟）
                <input defaultValue="10080" max="525600" min="60" name="check_frequency_minutes" required type="number" />
              </label>
              <label>
                内容保留
                <select name="content_retention_policy" required>
                  <option value="metadata_only">仅元数据</option>
                  <option value="minimal_excerpt">许可范围内最小摘录</option>
                </select>
              </label>
              <div className="wide-field">
                <button className="button button-approve" type="submit">登记来源</button>
              </div>
            </form>
          ) : (
            <div className="empty-state">当前管理员在本租户没有可配置的公司。</div>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">来源配置</p>
              <h2>{sources.length} 个受控来源</h2>
            </div>
            <span className="muted">真实运行需 Worker 与双开关</span>
          </div>
          <div className="review-list">
            {sources.length ? sources.map((source) => <SourceCard key={source.id} source={source} />) : <div className="empty-state">尚未登记来源。</div>}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">最近运行</p>
              <h2>后台检查审计</h2>
            </div>
            <span className="muted">最近 {runs.length} 次</span>
          </div>
          <div className="monitor-run-grid">
            {runs.length ? runs.slice(0, 12).map((run) => <RunCard key={run.id} run={run} />) : <div className="empty-state">尚无检查运行。</div>}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">人工入口</p>
              <h2>候选文档队列</h2>
            </div>
            <span className="muted">{candidates.filter((item) => item.processing_status === "pending").length} 条待处理</span>
          </div>
          <div className="review-list">
            {candidates.length ? candidates.map((candidate) => <CandidateCard candidate={candidate} key={candidate.id} />) : <div className="empty-state">当前没有候选文档。</div>}
          </div>
        </section>

        <section className="gap-panel review-security-note">
          <p className="eyebrow">安全边界</p>
          <p>
            来源、运行和候选均为本租户平台运营私有数据。个人用户与其他租户不可见；共享事实仍须经过现有人工晋升流程。
          </p>
        </section>
      </main>
    );
  } catch (error) {
    await redirectIfAuthenticationRequired(error, "/monitoring");
    const forbidden = error instanceof ApiError && error.status === 403;
    return (
      <main className="shell page-stack">
        <section className="hero">
          <p className="eyebrow">{forbidden ? "权限边界" : "读取失败"}</p>
          <h1>{forbidden ? "仅平台管理员可用" : "暂时无法读取来源监测数据"}</h1>
          <p>{forbidden ? "候选文档不是个人或普通机构用户可见数据。" : "请检查后端、数据库迁移和管理员身份。"}</p>
        </section>
      </main>
    );
  }
}
