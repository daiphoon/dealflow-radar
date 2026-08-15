import type { ReactNode } from "react";

const eventTypeLabels: Record<string, string> = {
  financial_operation: "财务与经营",
  financing_cap_table: "融资与股权",
  contract_commercial: "合同与商业进展",
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
  mixed: "有利有弊",
  unknown: "影响方向待确认",
};

const riskLabels: Record<string, string> = {
  none: "暂无显著风险",
  low: "低风险",
  moderate: "中等风险",
  high: "高风险",
  critical: "严重风险",
};

const freshnessLabels: Record<string, string> = {
  fresh: "数据较新",
  stale: "数据可能已过期",
  refreshing: "正在后台更新",
  unknown: "尚未确认",
  budget_deferred: "因预算限制暂缓更新",
};

const linkStatusLabels: Record<string, string> = {
  healthy: "链接正常",
  unchecked: "尚未自动检查",
  broken: "链接已失效",
  unavailable: "当前无法访问",
};

const glossary = [
  ["UC-MSCs", "脐带来源间充质干细胞"],
  ["NMPA", "国家药品监督管理局"],
  ["CDMO", "合同研发与生产服务"],
  ["CDE", "国家药品监督管理局药品审评中心"],
  ["FDA", "美国食品药品监督管理局"],
  ["DMF", "药品主文件，即向监管机构提交的药品生产和质量资料"],
  ["CGT", "细胞与基因治疗"],
  ["IND", "新药临床试验申请"],
  ["NDA", "新药上市申请"],
  ["GMP", "药品生产质量管理规范"],
  ["GLP", "药物非临床研究质量管理规范"],
  ["CRO", "合同研究服务"],
  ["IPO", "首次公开发行股票并上市"],
] as const;

const glossaryPattern = new RegExp(
  `\\b(${glossary.map(([term]) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\b`,
  "g",
);

const legacyWordingReplacements = new Map<string, string>([
  [
    "> 本报告由已审核的平台共享事实按固定模板生成，不含投资建议、机构私有数据或未确认线索。",
    "> 本报告根据平台已经审核的信息生成，不包含投资建议、机构私有数据或未确认线索。",
  ],
  ["## 已审核平台共享事件", "## 已审核的重要信息"],
  ["暂无已审核的平台共享事件。", "暂无已审核的重要信息。"],
  ["- 尚无平台共享公司快照。", "- 尚无可展示的数据概况。"],
  [
    "本报告是生成时点的只读快照；后续新增、纠正或撤回请以公司最新详情为准。",
    "本报告记录生成时的信息；后续新增、纠正或撤回请以公司最新详情为准。",
  ],
]);

function translatedFieldValue(field: string, value: string): string {
  const labels = {
    分类: eventTypeLabels,
    方向: directionLabels,
    风险级别: riskLabels,
    数据新鲜度: freshnessLabels,
  }[field];
  const fallbackLabels: Record<string, string> = {
    分类: "其他",
    方向: "影响方向待确认",
    风险级别: "风险待确认",
    数据新鲜度: "尚未确认",
  };
  return labels?.[value] ?? fallbackLabels[field] ?? "尚未确认";
}

function formatLegacyDateTime(value: string): string | null {
  const chinaTime = value.match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})? CST$/,
  );
  if (chinaTime) {
    return `${chinaTime[1]}年${chinaTime[2]}月${chinaTime[3]}日 ${chinaTime[4]}:${chinaTime[5]}`;
  }
  const normalized = value.endsWith(" CST") ? `${value.slice(0, -4)}+08:00` : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "long",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(date);
}

function localizeLegacyLine(line: string): string {
  const wordingReplacement = legacyWordingReplacements.get(line);
  if (wordingReplacement) return wordingReplacement;

  const fieldMatch = line.match(/^(- (分类|方向|风险级别|数据新鲜度)：)([a-z_]+)$/);
  if (fieldMatch) {
    const prefix = fieldMatch[2] === "数据新鲜度" ? "- 数据更新状态：" : fieldMatch[1];
    return `${prefix}${translatedFieldValue(fieldMatch[2], fieldMatch[3])}`;
  }

  const confidenceMatch = line.match(/^(- 可信度：)(0(?:\.\d+)?|1(?:\.0+)?)$/);
  if (confidenceMatch) {
    return `${confidenceMatch[1]}${Math.round(Number(confidenceMatch[2]) * 100)}%`;
  }

  const timeMatch = line.match(/^(- (?:最后检查时间|报告生成时间)：)(.+)$/);
  if (timeMatch) {
    const localized = formatLegacyDateTime(timeMatch[2]);
    if (localized) return `${timeMatch[1]}${localized}`;
  }

  const dateHeading = line.match(/^(#{1,3}\s+)?(\d{4})-(\d{2})-(\d{2})(｜.+)$/);
  const localizedDateLine = dateHeading
    ? `${dateHeading[1] ?? ""}${dateHeading[2]}年${dateHeading[3]}月${dateHeading[4]}日${dateHeading[5]}`
    : line;

  return localizedDateLine.replace(
    /(链接状态：|链接不开放；状态：)([a-z_]+)/g,
    (_, prefix: string, status: string) =>
      `${prefix}${linkStatusLabels[status] ?? "状态尚未确认"}`,
  );
}

function renderPlainText(
  text: string,
  keyPrefix: string,
  explainedTerms: Set<string>,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(glossaryPattern)) {
    const index = match.index ?? 0;
    if (index > cursor) nodes.push(text.slice(cursor, index));
    const term = match[0];
    const explanation = glossary.find(([candidate]) => candidate === term)?.[1];
    if (!explanation || explainedTerms.has(term)) {
      nodes.push(term);
    } else {
      explainedTerms.add(term);
      nodes.push(
        <span className="report-term" key={`${keyPrefix}-term-${index}`}>
          {term}
          <span className="report-term-explanation">（{explanation}）</span>
        </span>,
      );
    }
    cursor = index + term.length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function renderInline(
  text: string,
  keyPrefix: string,
  explainedTerms: Set<string>,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  const linkPattern = /<(https?:\/\/[^>\s]+)>/g;
  let cursor = 0;
  for (const match of text.matchAll(linkPattern)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      nodes.push(
        ...renderPlainText(text.slice(cursor, index), `${keyPrefix}-${index}`, explainedTerms),
      );
    }
    nodes.push(
      <a href={match[1]} key={`${keyPrefix}-link-${index}`} rel="noreferrer" target="_blank">
        查看来源 ↗
      </a>,
    );
    cursor = index + match[0].length;
  }
  if (cursor < text.length) {
    nodes.push(...renderPlainText(text.slice(cursor), `${keyPrefix}-tail`, explainedTerms));
  }
  return nodes;
}

function startsBlock(line: string): boolean {
  return (
    line === "" ||
    line === "---" ||
    /^#{1,3}\s/.test(line) ||
    line.startsWith("> ") ||
    line.startsWith("- ")
  );
}

export function ReportContent({ markdown }: { markdown: string }) {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  const explainedTerms = new Set<string>();
  let index = 0;

  while (index < lines.length) {
    const rawLine = lines[index].trim();
    const line = localizeLegacyLine(rawLine);
    if (!line) {
      index += 1;
      continue;
    }
    if (line === "---") {
      blocks.push(<hr key={`separator-${index}`} />);
      index += 1;
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const content = renderInline(heading[2], `heading-${index}`, explainedTerms);
      if (heading[1].length === 3) {
        blocks.push(<h4 key={`heading-${index}`}>{content}</h4>);
      } else {
        blocks.push(<h3 key={`heading-${index}`}>{content}</h3>);
      }
      index += 1;
      continue;
    }
    if (line.startsWith("> ")) {
      blocks.push(
        <blockquote key={`quote-${index}`}>
          {renderInline(line.slice(2), `quote-${index}`, explainedTerms)}
        </blockquote>,
      );
      index += 1;
      continue;
    }
    if (line.startsWith("- ")) {
      const items: ReactNode[] = [];
      while (index < lines.length) {
        const item = localizeLegacyLine(lines[index].trim());
        if (!item.startsWith("- ")) break;
        items.push(
          <li key={`item-${index}`}>
            {renderInline(item.slice(2), `item-${index}`, explainedTerms)}
          </li>,
        );
        index += 1;
      }
      blocks.push(<ul key={`list-${index}`}>{items}</ul>);
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length) {
      const nextLine = localizeLegacyLine(lines[index].trim());
      if (startsBlock(nextLine)) break;
      paragraphLines.push(nextLine);
      index += 1;
    }
    blocks.push(
      <p key={`paragraph-${index}`}>
        {renderInline(paragraphLines.join(" "), `paragraph-${index}`, explainedTerms)}
      </p>,
    );
  }

  return <article className="report-content">{blocks}</article>;
}
