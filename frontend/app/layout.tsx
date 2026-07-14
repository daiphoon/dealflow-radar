import type { Metadata } from "next";
import type { ReactNode } from "react";

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
            <a className="brand" href="/">
              原始股雷达
            </a>
            <span className="demo-badge">虚构数据 Demo</span>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
