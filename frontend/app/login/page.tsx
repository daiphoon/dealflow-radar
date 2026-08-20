import { redirect } from "next/navigation";
import Link from "next/link";

import { authProvider, phoneLoginEnabled, safeReturnPath } from "@/lib/auth-session";

import { completeLogin, requestLoginCode, requestPhoneLoginCode } from "./actions";

export const dynamic = "force-dynamic";

const errorMessages: Record<string, string> = {
  invalid_email: "请输入有效的邮箱地址。",
  invalid_phone: "请输入有效的中国大陆手机号。",
  invalid_code: "验证码无效或已经过期，请重新获取。",
  invitation_required: "该账户尚未获得本平台邀请，请联系管理员。",
  challenge_required: "CloudBase 要求额外安全验证；当前邀请制 V1 尚未支持图片验证码。",
  rate_limited: "请求过于频繁，请稍后再试。",
  unavailable: "身份服务暂时不可用，请稍后再试。",
  session_expired: "登录已过期，请重新获取验证码。",
  not_enabled: "当前环境尚未启用 CloudBase 身份认证。",
  phone_not_enabled: "当前环境尚未启用手机号登录，请使用邮箱验证码登录。",
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
    method?: string;
  }>;
}) {
  const query = await searchParams;
  const returnTo = safeReturnPath(query.next);
  if (authProvider !== "cloudbase" && !query.error) {
    redirect("/?auth=demo");
  }
  const verifyStep = query.step === "verify";
  const loginMethod = phoneLoginEnabled && query.method === "phone" ? "phone" : "email";
  const isPhoneLogin = loginMethod === "phone";
  const loginCopy = isPhoneLogin
    ? "仅已由管理员预先邀请并在身份服务中绑定的手机号可以登录。手机号不会写入本平台业务数据库。"
    : "仅已由管理员预先邀请的邮箱可以登录。身份核验完成后，基金、公司和审核权限仍由本平台服务端判断。";
  return (
    <main className="shell auth-page">
      <section className="auth-card">
        <p className="eyebrow">受邀用户登录</p>
        <h1>
          {verifyStep
            ? `输入${isPhoneLogin ? "手机" : "邮箱"}验证码`
            : "登录原始股雷达"}
        </h1>
        <p className="auth-copy">{loginCopy}</p>
        {!verifyStep && phoneLoginEnabled ? (
          <nav aria-label="登录方式" className="auth-method-switch">
            <a
              aria-current={isPhoneLogin ? undefined : "page"}
              className={isPhoneLogin ? "" : "active"}
              href={`/login?method=email&next=${encodeURIComponent(returnTo)}`}
            >
              邮箱验证码
            </a>
            <a
              aria-current={isPhoneLogin ? "page" : undefined}
              className={isPhoneLogin ? "active" : ""}
              href={`/login?method=phone&next=${encodeURIComponent(returnTo)}`}
            >
              手机验证码
            </a>
          </nav>
        ) : null}
        {query.sent ? (
          <div className="feedback feedback-success">
            验证码已发送（如该{isPhoneLogin ? "手机号" : "邮箱"}已受邀并已绑定）。
          </div>
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
            <a
              className="back-link"
              href={`/login?method=${loginMethod}&next=${encodeURIComponent(returnTo)}`}
            >
              重新获取验证码
            </a>
          </form>
        ) : isPhoneLogin ? (
          <form action={requestPhoneLoginCode} className="auth-form">
            <input name="next" type="hidden" value={returnTo} />
            <label htmlFor="login-phone">受邀手机号</label>
            <input
              autoComplete="tel"
              id="login-phone"
              inputMode="tel"
              maxLength={20}
              name="phone_number"
              pattern="(?:\+?86[ -]?)?1[3-9](?:[ -]?[0-9]){9}"
              placeholder="138 0000 0000"
              required
              type="tel"
            />
            <button className="button button-search" type="submit">
              发送短信验证码
            </button>
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
          登录不会迁移或复制现有业务数据，也不会调用天眼查。登录前请阅读
          <Link href="/trial-notice">《邀请测试说明与隐私告知》</Link>。
        </p>
      </section>
    </main>
  );
}
