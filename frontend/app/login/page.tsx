import { redirect } from "next/navigation";
import Link from "next/link";

import { authProvider, safeReturnPath } from "@/lib/auth-session";

import { completeLogin, requestLoginCode } from "./actions";

export const dynamic = "force-dynamic";

const errorMessages: Record<string, string> = {
  invalid_email: "请输入有效的邮箱地址。",
  invalid_code: "验证码无效或已经过期，请重新获取。",
  invitation_required: "该账户尚未获得本平台邀请，请联系管理员。",
  challenge_required: "CloudBase 要求额外安全验证；当前邀请制 V1 尚未支持图片验证码。",
  rate_limited: "请求过于频繁，请稍后再试。",
  unavailable: "身份服务暂时不可用，请稍后再试。",
  session_expired: "登录已过期，请重新验证邮箱。",
  not_enabled: "当前环境尚未启用 CloudBase 身份认证。",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{
    step?: string;
    sent?: string;
    error?: string;
    result?: string;
    next?: string;
  }>;
}) {
  const query = await searchParams;
  const returnTo = safeReturnPath(query.next);
  if (authProvider !== "cloudbase" && !query.error) {
    redirect("/?auth=demo");
  }
  const verifyStep = query.step === "verify";
  return (
    <main className="shell auth-page">
      <section className="auth-card">
        <p className="eyebrow">受邀用户登录</p>
        <h1>{verifyStep ? "输入邮箱验证码" : "登录原始股雷达"}</h1>
        <p className="auth-copy">
          仅已由管理员预先邀请的邮箱可以登录。CloudBase 只核验身份，基金、公司和审核权限仍由本平台服务端判断。
        </p>
        {query.sent ? (
          <div className="feedback feedback-success">验证码已发送（如该邮箱已受邀）。</div>
        ) : null}
        {query.result === "logged_out" ? (
          <div className="feedback feedback-success">已安全退出。</div>
        ) : null}
        {query.error && errorMessages[query.error] ? (
          <div className="feedback feedback-error">{errorMessages[query.error]}</div>
        ) : null}

        {verifyStep ? (
          <form action={completeLogin} className="auth-form">
            <input name="next" type="hidden" value={returnTo} />
            <label htmlFor="verification-code">6 位验证码</label>
            <input
              autoComplete="one-time-code"
              id="verification-code"
              inputMode="numeric"
              maxLength={6}
              name="verification_code"
              pattern="[0-9]{6}"
              placeholder="123456"
              required
            />
            <button className="button button-search" type="submit">
              确认登录
            </button>
            <a className="back-link" href={`/login?next=${encodeURIComponent(returnTo)}`}>
              重新获取验证码
            </a>
          </form>
        ) : (
          <form action={requestLoginCode} className="auth-form">
            <input name="next" type="hidden" value={returnTo} />
            <label htmlFor="login-email">受邀邮箱</label>
            <input
              autoComplete="email"
              id="login-email"
              maxLength={320}
              name="email"
              placeholder="you@example.com"
              required
              type="email"
            />
            <button className="button button-search" type="submit">
              发送验证码
            </button>
          </form>
        )}
        <p className="auth-note">
          登录不会迁移或复制现有 PostgreSQL 数据，也不会调用天眼查。登录前请阅读
          <Link href="/trial-notice">《邀请测试说明与隐私告知》</Link>。
        </p>
      </section>
    </main>
  );
}
