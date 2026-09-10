"use client";

import Link from "next/link";
import { TenderObservations } from "@/components/tender-observations";
import { useEffect, useMemo, useRef, useState } from "react";

import { loadPersonalCompanyChanges } from "@/app/personal-actions";
import type { Event, PersonalCompanyView } from "@/lib/api";

const FIRST_VIEW_LOOKBACK_DAYS = 90;
const PRIMARY_CHANGE_LIMIT = 3;

const riskLabels: Record<string, string> = {
  none: "暂无显著风险",
  low: "低风险",
  moderate: "需要留意",
  high: "高风险",
  critical: "严重风险",
};

function formatDate(value: string | null): string {
  if (!value) return "日期未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function eventDate(event: Event): string {
  return event.occurred_on ?? event.occurred_at ?? event.published_at ?? event.published_on ?? event.observed_at;
}

function isMaterialChange(event: Event): boolean {
  return event.display_kind === "confirmed_change" || event.publication_route === "deterministic_change";
}

function withinDays(event: Event, reference: string, days: number): boolean {
  const eventTime = new Date(eventDate(event)).getTime();
  const referenceTime = new Date(reference).getTime();
  return Number.isFinite(eventTime) && eventTime >= referenceTime - days * 24 * 60 * 60 * 1000;
}

function importanceLabel(score: number): string {
  if (score >= 75) return "非常重要";
  if (score >= 60) return "重要";
  return "值得留意";
}

function confidenceLabel(score: string): string {
  const value = Number(score);
  if (value >= 0.9) return "证据可信度较高";
  if (value >= 0.75) return "证据可信度中等";
  return "仍需结合证据判断";
}

function sortChanges(events: Event[]): Event[] {
  return [...events].sort((left, right) => {
    const materiality = right.materiality_score - left.materiality_score;
    if (materiality !== 0) return materiality;
    return new Date(eventDate(right)).getTime() - new Date(eventDate(left)).getTime();
  });
}

function uniqueChanges(events: Event[]): Event[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    if (seen.has(event.id)) return false;
    seen.add(event.id);
    return true;
  });
}

function InvestorChangeCard({ event }: { event: Event }) {
  const changeField = event.facts.find((fact) => fact.name === "变化字段")?.value;
  const beforeValue = event.facts.find((fact) => fact.name === "变更前")?.value;
  const afterValue = event.facts.find((fact) => fact.name === "变更后")?.value;
  const headline = event.analysis?.headline ?? event.title;
  const investorMeaning = event.analysis?.why_it_matters ?? event.summary;

  return (
    <article className="investor-change-card" id={`change-${event.id}`}>
      <div className="investor-change-meta">
        <span>{importanceLabel(event.materiality_score)}</span>
        <time>{formatDate(eventDate(event))}</time>
      </div>
      <h3>{headline}</h3>
      <section className="investor-impact-summary">
        <strong>这对股东可能意味着什么</strong>
        <p>{investorMeaning}</p>
      </section>
      <div className="investor-change-signals" aria-label="变化判断依据">
        <span>{confidenceLabel(event.confidence_score)}</span>
        <span>{riskLabels[event.risk_severity] ?? "风险影响待判断"}</span>
        <span>{event.evidence.length} 条证据</span>
      </div>

      <details className="investor-change-details">
        <summary>查看变化详情与证据</summary>
        <div className="investor-change-detail-body">
          <div>
            <strong>发生了什么变化</strong>
            <p>{event.analysis?.what_changed ?? event.summary}</p>
          </div>
          {changeField && beforeValue && afterValue ? (
            <div className="change-comparison" aria-label={`${changeField}前后变化`}>
              <span>{changeField}</span>
              <div>
                <p>
                  <small>变更前</small>
                  <strong>{beforeValue}</strong>
                </p>
                <span aria-hidden="true">→</span>
                <p>
                  <small>变更后</small>
                  <strong>{afterValue}</strong>
                </p>
              </div>
            </div>
          ) : null}
          {event.analysis?.potential_impacts.length ? (
            <div>
              <strong>可能影响</strong>
              <ul>
                {event.analysis.potential_impacts.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {event.analysis?.uncertainties.length ? (
            <div>
              <strong>目前还不能确定</strong>
              <ul>
                {event.analysis.uncertainties.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : event.uncertainties.length ? (
            <div>
              <strong>目前还不能确定</strong>
              <ul>
                {event.uncertainties.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {event.analysis?.follow_up_items.length ? (
            <div>
              <strong>后续值得关注</strong>
              <ul>
                {event.analysis.follow_up_items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <TenderObservations observations={event.tender_observations ?? []} />
          <div className="investor-evidence-list">
            <strong>证据来源</strong>
            {event.evidence.map((evidence) => {
              const sourceUrl = evidence.final_url ?? evidence.canonical_url;
              return (
                <div className="investor-evidence-item" key={evidence.id}>
                  <div>
                    <span>{evidence.source_name}</span>
                    <small>{evidence.title}</small>
                    {event.tender_observations?.length ? <blockquote>{evidence.excerpt}</blockquote> : null}
                  </div>
                  <div>
                    {evidence.detail_available ? (
                      <Link href={`/evidence/${encodeURIComponent(evidence.id)}`}>
                        查看平台证据详情
                      </Link>
                    ) : null}
                    {evidence.link_display_allowed ? (
                      <a href={sourceUrl} rel="noreferrer" target="_blank">
                        查看原始来源 ↗
                      </a>
                    ) : (
                      <span className="muted">原始链接暂不可直接开放</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {event.analysis ? (
            <p className="analysis-disclaimer">{event.analysis.disclaimer}</p>
          ) : (
            <p className="analysis-pending">
              变化事实已核实；辅助解读尚未生成，不影响查看变化和证据。
            </p>
          )}
        </div>
      </details>
    </article>
  );
}

export function PersonalChangePanel({
  companyId,
  materialChanges,
  baselineEventIds,
}: {
  companyId: string;
  materialChanges: Event[];
  baselineEventIds: string[];
}) {
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

  const presentation = useMemo(() => {
    const allChanges = sortChanges(materialChanges.filter(isMaterialChange));
    if (!view) {
      return {
        eyebrow: "投资者首先查看",
        heading: "近期重要变化",
        context: failed ? "个人查看进度暂不可用" : "正在读取个人查看进度…",
        intro: failed
          ? "以下显示最近已核实的重要变化；本次没有更新个人查看水位。"
          : "正在确定首次查看或自上次查看以来的时间范围。",
        active: failed ? allChanges : [],
        additional: failed ? allChanges.slice(PRIMARY_CHANGE_LIMIT) : [],
        newReferenceCount: 0,
        loading: !failed,
      };
    }

    // 查看水位仅决定时间范围，实际内容始终来自本页同一份已授权列表。
    const newEventIds = new Set(view.new_events.map((event) => event.id));
    const newMaterialChanges = allChanges.filter((event) => newEventIds.has(event.id));
    const recentNinetyDayChanges = allChanges.filter((event) =>
      withinDays(event, view.viewed_at, FIRST_VIEW_LOOKBACK_DAYS),
    );
    const active = view.first_view
      ? newMaterialChanges
      : newMaterialChanges.length > 0
        ? newMaterialChanges
        : recentNinetyDayChanges;
    const activeIds = new Set(active.map((event) => event.id));
    const additional = uniqueChanges([
      ...active.slice(PRIMARY_CHANGE_LIMIT),
      ...allChanges.filter((event) => !activeIds.has(event.id)),
    ]);

    return {
      eyebrow: "投资者首先查看",
      heading: view.first_view
        ? `近 ${FIRST_VIEW_LOOKBACK_DAYS} 天的重要变化`
        : newMaterialChanges.length > 0
          ? "自上次查看以来的重要变化"
          : `近 ${FIRST_VIEW_LOOKBACK_DAYS} 天的重要变化回顾`,
      context: view.first_view
        ? "首次查看"
        : `上次查看 ${formatDate(view.previous_viewed_at)}`,
      intro:
        !view.first_view && newMaterialChanges.length === 0
          ? "自上次查看以来，平台暂无新增的已核实重要变化记录；这不代表公司没有变化。以下保留已有记录，方便回顾。"
          : "只呈现已经发生变化、并可能影响投资判断的内容。",
      active,
      additional,
      newReferenceCount: baselineEventIds.filter((id) => newEventIds.has(id)).length,
      loading: false,
    };
  }, [baselineEventIds, failed, materialChanges, view]);

  const primaryChanges = presentation.active.slice(0, PRIMARY_CHANGE_LIMIT);

  return (
    <section className="panel investor-priority-panel" id="company-current-changes">
      <div className="panel-heading investor-priority-heading">
        <div>
          <p className="eyebrow">{presentation.eyebrow}</p>
          <h2>{presentation.heading}</h2>
        </div>
        <span className="status status-fresh">{presentation.context}</span>
      </div>
      <p className="section-intro">{presentation.intro}</p>

      {presentation.loading ? (
        <div className="empty-state">正在整理当前账户需要优先查看的重要变化…</div>
      ) : primaryChanges.length === 0 ? (
        <div className="empty-state">
          当前时间范围内暂无达到展示门槛的重要变化。首次资料只用于建立比较基线，不代表公司经营没有变化。
        </div>
      ) : (
        <div className="investor-change-list">
          {primaryChanges.map((event) => (
            <InvestorChangeCard event={event} key={event.id} />
          ))}
        </div>
      )}

      {presentation.additional.length > 0 ? (
        <details className="older-change-list">
          <summary>查看其余 {presentation.additional.length} 条较早或次要变化</summary>
          <div className="investor-change-list">
            {presentation.additional.map((event) => (
              <InvestorChangeCard event={event} key={event.id} />
            ))}
          </div>
        </details>
      ) : null}

      {presentation.newReferenceCount > 0 ? (
        <p className="secondary-update-note">
          另有 {presentation.newReferenceCount} 条新基础资料已归入页面下方的“公司资料与历史记录”。
          <a href="#company-records">前往查看</a>
        </p>
      ) : null}
    </section>
  );
}
