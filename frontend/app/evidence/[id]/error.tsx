"use client";

import Link from "next/link";

export default function EvidenceDetailError({ reset }: { reset: () => void }) {
  return (
    <main className="shell page-stack evidence-detail-shell">
      <section className="panel friendly-error-panel">
        <p className="eyebrow">平台证据详情</p>
        <h1>暂时无法读取这条证据</h1>
        <p>
          可能是登录状态刚刚过期，或者证据服务暂时不可用。你可以重新尝试；如果仍然失败，请返回公司查询后再打开。
        </p>
        <div className="friendly-error-actions">
          <button className="button button-approve" onClick={() => reset()} type="button">
            重新尝试
          </button>
          <Link className="button button-secondary" href="/">
            返回公司查询
          </Link>
        </div>
      </section>
    </main>
  );
}
