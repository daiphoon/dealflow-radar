import type { PersonalCompanyRequest } from "./api";

const labels: Record<string, string> = {
  pending: "等待资料核对",
  in_review: "待人工核对",
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
  completed: "本轮已结束",
  rejected: "未受理",
  failed: "处理失败",
};

export function requestState(request: Pick<PersonalCompanyRequest, "status" | "last_error_code" | "research_job_status">) {
  let label = labels[request.status] ?? request.status;
  if (request.status === "in_review") {
    if (request.last_error_code === "identity_evidence_missing") label = "待补充主体资料";
    if (request.last_error_code === "curator_identity_confirmed") label = "主体已确认，待安排更新";
  }
  const poll = ["identity_queued", "identity_checking", "research_queued", "researching", "cancel_requested"].includes(request.status)
    || (request.status === "partial" && ["queued", "running"].includes(request.research_job_status ?? ""));
  return { label, poll };
}
