import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { cookies } from "next/headers";

import { ACCESS_TOKEN_COOKIE, authProvider } from "@/lib/auth-session";
import { getAuthenticationMe } from "@/lib/api";

import { logout } from "./login/actions";

import "./globals.css";

export const metadata: Metadata = {
  title: "原始股雷达",
  description: "可追溯的未上市企业投后信息监测平台",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const hasSession = (await cookies()).has(ACCESS_TOKEN_COOKIE);
  let roles: string[] = [];
  if (authProvider === "demo" || hasSession) {
    try {
      roles = (await getAuthenticationMe()).roles;
    } catch {
      // 导航入口只做体验提示；读取失败时默认隐藏，后端仍独立强制权限。
    }
  }
  const canReview = roles.includes("reviewer");
  const canMonitor = roles.includes("platform_admin");
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
                {canReview ? <Link href="/reviews">人工审核</Link> : null}
                {canMonitor ? <Link href="/monitoring">来源监测</Link> : null}
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
              <span className="demo-badge">邀请测试</span>
            </div>
          </div>
        </header>
        {children}
        <footer className="site-footer">
          <div className="shell site-footer-inner">
            <span>邀请制测试</span>
            <Link href="/trial-notice">邀请测试说明与隐私告知</Link>
          </div>
        </footer>
      </body>
    </html>
  );
}
