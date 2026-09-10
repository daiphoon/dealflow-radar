import type { WatchlistMonitor } from "../lib/api";

const labels: Record<string, string> = {
  disabled: "低频检查未启用",
  not_scheduled: "等待安排首次检查",
  never_checked: "尚未检查",
  queued: "已排队",
  checking: "检查中",
  succeeded: "本轮检查已完成",
  budget_deferred: "预算不足，已延后",
  failed: "检查失败，等待重试",
  partial: "本轮仅完成部分检查",
  cancelled: "检查已停止",
};

function date(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "暂无记录";
}

export function WatchlistMonitorStatus({ monitoring }: { monitoring: WatchlistMonitor | null }) {
  if (!monitoring) return null;
  return (
    <div className="card-event">
      <strong>{labels[monitoring.status] ?? "等待检查"}</strong>
      <p className="muted">检查范围：经营、融资、合同/中标</p>
      {monitoring.status !== "disabled" && (
        <>
          <p>最近尝试：{date(monitoring.last_attempt_at)}</p>
          <p>最近成功检查：{date(monitoring.last_successful_check_at)}</p>
          <p>下次计划：{date(monitoring.next_check_at)}</p>
          <p className="muted">仅表示上述范围的检查进度；未发现新资料不代表公司经营正常。</p>
        </>
      )}
    </div>
  );
}
