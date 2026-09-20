import Link from "next/link";

import { ResearchCoverage } from "@/components/research-coverage";
import type { PersonalCompanyRequest } from "@/lib/api";

const moduleLabels: Record<string, string> = {
  financial_operation: "财务与经营",
  financing_cap_table: "融资与股权",
  contract_commercial: "合同与商业进展",
  product_technology: "产品与技术",
  governance_people: "治理与人员",
  legal_compliance: "司法与合规",
  capacity_assets: "产能与资产",
  exit_liquidity: "退出与流动性",
};

const moduleStatusLabels: Record<string, string> = {
  pending: "等待检查",
  running: "正在检查",
  search_completed: "已完成相关主题检索，正文核对中",
  completed: "当时已形成候选资料，不代表事实已核实",
  no_data: "检查情况未完整记录",
  failed: "本次检查未完成",
};

const terminalStatuses = new Set(["completed", "cancelled", "rejected", "failed"]);

function finishedAt(value: string | null): string {
  if (!value) return "尚未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export function RequestResearchResult({ request }: { request: PersonalCompanyRequest }) {
  const current = request.current_company_content;
  const result = request.research_result;
  const terminal = terminalStatuses.has(request.status);
  const modules = Object.entries(request.research_modules).filter(([code]) => code in moduleLabels);

  return (
    <>
      {current && request.company_id ? (
        <section aria-label="当前可查看内容">
          <strong>当前可查看内容</strong>
          <p>
            已核实变化（含历史）：{current.confirmed_changes} 条；
            基础资料：{current.baseline_facts} 条；
            待核实线索：{current.unconfirmed_leads} 条。
          </p>
          {current.confirmed_changes + current.baseline_facts === 0 ? (
            <p className="muted">
              {current.unconfirmed_leads > 0
                ? "当前尚无已核实事实；线索仅供进一步核对，不能作为已确认结论。"
                : "当前暂无可展示的共享事实或线索，不代表公司没有重要变化。"}
            </p>
          ) : null}
          <Link prefetch={false} href={`/companies/${request.company_id}`}>查看当前公司内容</Link>
        </section>
      ) : null}

      {terminal ? (
        <>
          {result ? <p className="muted">本次资料覆盖有限，不代表公司没有重要变化。</p> : null}
          <details>
            <summary>查看本次查询过程与限制（历史记录）</summary>
            <p className="muted">
              以下记录本次任务当时的情况，不随后续补充资料改写。当前内容请以公司档案为准。
            </p>
            <p>{request.status_message}</p>
            {result ? (
              <>
                <p>本轮结束时间：{finishedAt(result.finished_at)}</p>
                <ul aria-label="本次资料覆盖说明">
                  {(result.coverage_summary?.length ? result.coverage_summary : [
                    "各类信息的检查情况未完整记录，不能据此判断某类信息不存在。",
                  ]).map((item) => <li key={item}>{item}</li>)}
                </ul>
                <ResearchCoverage items={result.category_coverage} />
                {result.limitations.length > 0 ? (
                  <ul aria-label="读取与证据限制">
                    {result.limitations.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                ) : null}
              </>
            ) : null}
          </details>
        </>
      ) : (
        <>
          <p>{request.status_message}</p>
          {modules.length > 0 ? (
            <ul className="research-module-list" aria-label="研究模块进度">
              {modules.map(([code, status]) => (
                <li key={code}>
                  <span>{moduleLabels[code]}</span>
                  <strong>{moduleStatusLabels[status] ?? "检查情况未完整记录"}</strong>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      )}
    </>
  );
}
