"use client";

import { useEffect, useId, useRef, useState } from "react";

import type { CompanySuggestion } from "@/lib/api";

function resultHref(company: CompanySuggestion): string {
  return `/?q=${encodeURIComponent(company.legal_name)}`;
}

export function CompanySearchForm({
  defaultValue,
  suggestionsEnabled,
}: {
  defaultValue: string;
  suggestionsEnabled: boolean;
}) {
  const [query, setQuery] = useState(defaultValue);
  const [suggestions, setSuggestions] = useState<CompanySuggestion[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [engaged, setEngaged] = useState(false);
  const listboxId = useId();
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setQuery(defaultValue);
    setSuggestions([]);
    setStatus("idle");
    setOpen(false);
    setActiveIndex(-1);
    setEngaged(false);
  }, [defaultValue]);

  useEffect(() => {
    const normalizedQuery = query.trim();
    if (!engaged || !suggestionsEnabled || [...normalizedQuery].length < 2) {
      setSuggestions([]);
      setStatus("idle");
      setOpen(false);
      setActiveIndex(-1);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setStatus("loading");
      try {
        const response = await fetch(
          `/api/company-suggestions?q=${encodeURIComponent(normalizedQuery)}`,
          { cache: "no-store", credentials: "same-origin", signal: controller.signal },
        );
        if (!response.ok) throw new Error(`suggestions failed: ${response.status}`);
        const payload = (await response.json()) as CompanySuggestion[];
        setSuggestions(payload);
        setStatus("ready");
        setOpen(true);
        setActiveIndex(-1);
      } catch (error) {
        if (controller.signal.aborted) return;
        setSuggestions([]);
        setStatus("error");
        setOpen(true);
        setActiveIndex(-1);
      }
    }, 300);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [engaged, query, suggestionsEnabled]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (!open || suggestions.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => (current + 1) % suggestions.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => (current <= 0 ? suggestions.length - 1 : current - 1));
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      window.location.assign(resultHref(suggestions[activeIndex]));
    }
  }

  return (
    <form className="search-form" method="get" role="search">
      <label htmlFor="company-query">公司名称、统一社会信用代码或已核实别名</label>
      <div className="search-control-row">
        <div
          className="company-search-autocomplete"
          onBlur={(event) => {
            if (!rootRef.current?.contains(event.relatedTarget as Node | null)) {
              setOpen(false);
              setActiveIndex(-1);
            }
          }}
          ref={rootRef}
        >
          <input
            aria-activedescendant={
              activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined
            }
            aria-autocomplete="list"
            aria-controls={open && suggestions.length > 0 ? listboxId : undefined}
            aria-expanded={open}
            autoComplete="off"
            id="company-query"
            maxLength={240}
            name="q"
            onChange={(event) => {
              setEngaged(true);
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={() => {
              setEngaged(true);
              if (status !== "idle") setOpen(true);
            }}
            onKeyDown={handleKeyDown}
            placeholder="例如：博腾生物"
            required
            role="combobox"
            value={query}
          />
          {open && status !== "idle" ? (
            <div className="company-suggestion-popover">
              {status === "loading" ? (
                <p className="company-suggestion-message">正在查找可能匹配的公司……</p>
              ) : status === "error" ? (
                <p className="company-suggestion-message">
                  暂时无法加载建议，仍可输入完整名称后点击查询。
                </p>
              ) : suggestions.length === 0 ? (
                <p className="company-suggestion-message">
                  暂未找到候选，可继续输入更完整的名称。
                </p>
              ) : (
                <ul aria-label="可能匹配的公司" id={listboxId} role="listbox">
                  {suggestions.map((company, index) => (
                    <li key={company.id} role="presentation">
                      <a
                        aria-selected={activeIndex === index}
                        className={activeIndex === index ? "is-active" : undefined}
                        href={resultHref(company)}
                        id={`${listboxId}-option-${index}`}
                        onMouseEnter={() => setActiveIndex(index)}
                        role="option"
                      >
                        <strong>{company.legal_name}</strong>
                        <span>
                          {company.registered_region ?? "注册地区未知"} · 信用代码：
                          {company.credit_code ?? "暂未收录"}
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </div>
        <button className="button button-search" type="submit">
          查询
        </button>
      </div>
      <span className="search-help">输入至少两个字可查看候选，最终请以工商全称和信用代码确认。</span>
    </form>
  );
}
