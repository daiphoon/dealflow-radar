"use client";

import { useEffect, useRef, useState } from "react";

import { loadPersonalCompanyChanges } from "@/app/personal-actions";
import type { PersonalCompanyView } from "@/lib/api";

const riskLabels: Record<string, string> = {
  none: "无显著风险",
  low: "低风险",
  moderate: "中等风险",
  high: "高风险",
  critical: "严重风险",
};

function formatDate(value: string | null): string {
  if (!value) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export function PersonalChangePanel({ companyId }: { companyId: string }) {
  const requestedCompanyId = useRef<string | null>(null);
  const [view, setView] = useState<PersonalCompanyView | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (requestedCompanyId.current === companyId) return;
    requestedCompanyId.current = companyId;
    setView(null);
    setFailed(false);
    loadPersonalCompanyChanges(companyId)
      .then((nextView) => {
        if (requestedCompanyId.current === companyId) setView(nextView);
      })
      .catch(() => {
        if (requestedCompanyId.current === companyId) setFailed(true);
      });
  }, [companyId]);

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">个人回访层</p>
          <h2>自上次查看以来的新增事实</h2>
        </div>
        <span className="muted">
          {view
            ? view.first_view
              ? "首次查看"
              : `上次查看 ${formatDate(view.previous_viewed_at)}`
            : "正在记录本次查看…"}
        </span>
      </div>
      {failed ? (
        <div className="empty-state">暂时无法读取个人查看水位；公司共享详情不受影响。</div>
      ) : !view ? (
        <div className="empty-state">正在检查新增的已审核共享事件…</div>
      ) : view.first_view ? (
        <div className="empty-state">
          已建立当前账户的查看基线；以后新增并通过审核的平台共享事件会显示在这里。
        </div>
      ) : view.new_events.length === 0 ? (
        <div className="empty-state">自上次查看以来暂无新增的已审核共享事件。</div>
      ) : (
        <div className="timeline">
          {view.new_events.map((event) => {
            const eventDate = event.occurred_at ?? event.published_at ?? event.published_on;
            return (
              <article className="event-card" key={event.id}>
                <div className="event-meta">
                  <span>{formatDate(eventDate)}</span>
                  <span className={`risk risk-${event.risk_severity}`}>
                    {riskLabels[event.risk_severity] ?? event.risk_severity}
                  </span>
                </div>
                <h3>{event.title}</h3>
                <p>{event.summary}</p>
                {event.evidence.map((evidence) =>
                  evidence.link_display_allowed ? (
                    <a
                      className="source-link"
                      href={evidence.final_url ?? evidence.canonical_url}
                      key={evidence.id}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {evidence.source_name} ↗
                    </a>
                  ) : (
                    <span className="muted" key={evidence.id}>
                      {evidence.source_name} · 来源链接不可开放
                    </span>
                  ),
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
