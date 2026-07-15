import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "原始股雷达 Demo",
  description: "可追溯的未上市企业投后信息监测 Demo",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="site-header">
          <div className="shell header-inner">
            <Link className="brand" href="/">
              原始股雷达
            </Link>
            <div className="header-actions">
              <nav className="site-nav" aria-label="主导航">
                <Link href="/">公司组合</Link>
                <Link href="/reviews">人工审核</Link>
              </nav>
              <span className="demo-badge">Demo / Validation</span>
            </div>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
