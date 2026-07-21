import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { cookies } from "next/headers";

import { ACCESS_TOKEN_COOKIE, authProvider } from "@/lib/auth-session";

import { logout } from "./login/actions";

import "./globals.css";

export const metadata: Metadata = {
  title: "原始股雷达 Demo",
  description: "可追溯的未上市企业投后信息监测 Demo",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const hasSession = (await cookies()).has(ACCESS_TOKEN_COOKIE);
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
                <Link href="/">公司查询</Link>
                <Link href="/watchlist">我的关注</Link>
                <Link href="/reports">我的报告</Link>
                <Link href="/reviews">人工审核</Link>
                <Link href="/monitoring">来源监测</Link>
              </nav>
              {authProvider === "cloudbase" ? (
                hasSession ? (
                  <form action={logout}>
                    <button className="header-auth-button" type="submit">
                      退出
                    </button>
                  </form>
                ) : (
                  <Link className="header-auth-link" href="/login">
                    登录
                  </Link>
                )
              ) : null}
              <span className="demo-badge">Demo / Validation</span>
            </div>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
