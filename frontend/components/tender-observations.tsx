import type { TenderObservation } from "@/lib/api";

function label(item: TenderObservation): string {
  if (!item.evidence_available) return "该版本证据已撤回或不可用";
  if (item.confirmed) return item.is_current ? "当前已核实版本" : "历史已核实版本";
  if (item.observation_kind === "correction_candidate") return "更正候选 · 待核实";
  if (item.observation_kind === "conflicting") return "冲突候选 · 待核实";
  if (item.observation_kind === "incomplete") return "字段不完整 · 待核实";
  if (item.observation_kind === "same_facts") return "同事实补充来源 · 不代表独立确认";
  return "公告候选 · 待核实";
}

export function TenderObservations({ observations, expanded = false }: {
  observations: TenderObservation[];
  expanded?: boolean;
}) {
  if (!observations.length) return null;
  return (
    <details className="tender-history" open={expanded}>
      <summary>查看来源记录与修订历史（{observations.length} 份）</summary>
      <p className="muted">每份来源记录独立保留；转载数量不代表独立确认，更正候选不会自动替换已核实事实。</p>
      {observations.map((item, index) => (
        <section key={`${item.observation_id ?? item.fact_version}-${index}`} aria-label={label(item)}>
          <strong>{label(item)}</strong>
          <p>公告所述事件日期：{item.occurred_on ?? "未知（不以来源发布时间补填）"}</p>
          {item.evidence_available ? (
            <dl>{item.facts.map((fact) => (
              <div key={fact.name}><dt>{fact.name}</dt><dd>{fact.value}{fact.unit ? ` ${fact.unit}` : ""}</dd></div>
            ))}</dl>
          ) : <p>暂不展示依赖该证据的字段和解读；历史记录仍保留。</p>}
        </section>
      ))}
    </details>
  );
}
