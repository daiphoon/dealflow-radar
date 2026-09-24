"use client";

import { useState, type ReactNode } from "react";

export type BrowseRecord = { id: string; category: string; status: string; occurred: string | null; content: ReactNode };
export type RecordFilters = { category: string; status: string; period: string };
const PAGE_SIZE = 20;

export function filterRecords(records: BrowseRecord[], filters: RecordFilters, reference: number): BrowseRecord[] {
  return records.filter((record) => {
    if (filters.category && record.category !== filters.category) return false;
    if (filters.status && record.status !== filters.status) return false;
    if (!filters.period) return true;
    const time = record.occurred ? Date.parse(record.occurred) : NaN;
    if (filters.period === "unknown") return !Number.isFinite(time);
    return Number.isFinite(time) && time <= reference && time >= reference - Number(filters.period) * 86400000;
  });
}

export function RecordBrowser({ records, reference }: { records: BrowseRecord[]; reference: number }) {
  const [filters, setFilters] = useState<RecordFilters>({ category: "", status: "", period: "" });
  const [page, setPage] = useState(0);
  const selected = filterRecords(records, filters, reference);
  const pages = Math.max(1, Math.ceil(selected.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const update = (key: keyof RecordFilters, value: string) => { setFilters({ ...filters, [key]: value }); setPage(0); };
  const categories = [...new Set(records.map((record) => record.category))];
  const statuses = [...new Set(records.map((record) => record.status))];
  return (
    <div>
      <div className="record-filters">
        {categories.length > 1 ? <label>资料类型 <select value={filters.category} onChange={(event) => update("category", event.target.value)}>
          <option value="">全部类型</option>{categories.map((value) => <option key={value}>{value}</option>)}
        </select></label> : null}
        {statuses.length > 1 ? <label>资料状态 <select value={filters.status} onChange={(event) => update("status", event.target.value)}>
          <option value="">全部状态</option>{statuses.map((value) => <option key={value}>{value}</option>)}
        </select></label> : null}
        <label>发生时间 <select value={filters.period} onChange={(event) => update("period", event.target.value)}>
          <option value="">全部时间</option><option value="30">近 30 天</option><option value="90">近 90 天</option><option value="unknown">日期未公开</option>
        </select></label>
      </div>
      <p role="status">共 {records.length} 条，筛选后 {selected.length} 条；本页 {selected.length ? current * PAGE_SIZE + 1 : 0}—{Math.min((current + 1) * PAGE_SIZE, selected.length)} 条</p>
      <div className="timeline">{selected.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE).map((record) => <div key={record.id}>{record.content}</div>)}</div>
      {!selected.length ? <p>当前筛选条件下没有资料；可选择全部时间或日期未公开。</p> : null}
      {pages > 1 ? <nav aria-label="资料翻页">
        <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>上一页</button>
        <span> 第 {current + 1} / {pages} 页 </span>
        <button type="button" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>下一页</button>
      </nav> : null}
    </div>
  );
}
