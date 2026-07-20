# ADR-0012：CloudBase 仅负责身份认证，本地数据库继续负责业务授权

- 状态：已接受
- 日期：2026-07-20
- 关联：ADR-0005、ADR-0009

## 背景

现有 API 通过可伪造的 `X-Demo-User-Id` 注入测试身份，只适合本机 Demo，不能保护真实用户、审核工作台和私有基金数据。项目负责人已否决 Clerk，决定先验证腾讯云 CloudBase 身份认证；同时明确禁止借机把 PostgreSQL、业务权限、RLS、基金或租户模型迁移到 CloudBase。

首批验证采用邀请制邮箱验证码：先覆盖无基金个人、基金用户、其他租户用户和平台管理员四类内部角色，再决定是否邀请 5—10 名个人用户。

## 决策

1. CloudBase 是身份提供方，只回答“登录者是谁”。本地 `users`、`user_role_assignments`、`fund_access_grants`、租户状态、资源作用域和 PostgreSQL RLS 继续回答“该用户能看什么、能做什么”。CloudBase `groups`、前端字段或 Cookie 内容不得授予业务角色。
2. V1 使用 CloudBase 官方 HTTP API 的邮箱验证码登录，发送验证码时固定 `target=USER`。CloudBase 中不存在的账户不能自行注册；本地数据库中也必须已经存在唯一、启用的受邀用户。
3. 只有刚完成 CloudBase 邮箱验证码交换的登录请求，才可用 `/user/me` 返回的邮箱匹配唯一的 active 本地用户，并保存 `(auth_provider, auth_subject)`。普通 Bearer 请求和 refresh token 不能建立首次绑定；后续只以稳定 subject 为准，不因邮箱变更自动换绑。相同邮箱命中零个或多个 tenant、本地用户已绑定其他 subject、用户或 tenant 停用时均失败关闭，等待管理员处理。
4. 浏览器不保存可由 JavaScript 读取的访问令牌。Next.js 服务端登录动作把 CloudBase access/refresh token 存入 `HttpOnly`、`SameSite=Strict` Cookie；生产环境再加 `Secure`。服务端渲染请求从 Cookie 取 token，以 `Authorization: Bearer` 调用 FastAPI。
5. FastAPI 在 `AUTH_PROVIDER=cloudbase` 时完全忽略 `X-Demo-User-Id`，通过 CloudBase `GET /auth/v1/user/me` 核验 token、active 状态、subject 和顶层邮箱，再加载本地用户并设置既有 RLS 上下文。真实原生邮箱账户的 `providers` 字段不保证是列表，且在控制台语义中与第三方身份源分开，因此不得把该字段作为邮箱验证码登录的必要条件；首次邮箱映射的控制证明来自刚完成的验证码交换。`AUTH_PROVIDER=demo` 仅为本地开发和默认 CI 保留，不得作为对外环境配置。
6. access token 到期后，Next.js 使用只存在于 `HttpOnly` Cookie 的 refresh token 调用官方刷新接口并轮换两枚 token。退出时先请求 CloudBase 撤销会话，再清除本地 Cookie；即使 Provider 暂时不可用，本地 Cookie 仍清除，失败写入审计并等待 access token 自然过期。
7. 新增追加式 `authentication_audit_logs`，记录本地身份绑定、会话开始、刷新和结束。只保存 provider subject 的 SHA-256，不保存 access token、refresh token、验证码或完整请求正文；应用角色没有更新或删除策略。
8. 认证调用是建立登录会话所必需的身份基础设施调用，不属于天眼查、搜索、模型或自动刷新业务调用。`EXTERNAL_CALLS_ENABLED`、`PAID_API_CALLS_ENABLED`、`AUTO_REFRESH_ENABLED` 和 `AUTO_PUBLISH_ENABLED` 继续保持关闭；CloudBase 是否启用只由 `AUTH_PROVIDER=cloudbase` 与有效环境配置共同决定。
9. CloudBase 只接入身份认证。V1 不迁移 PostgreSQL、FastAPI、Next.js、业务权限、RLS、Worker、文件、云函数或租户/基金模型，也不同时开发天眼查即时报告。

## 接口与失败语义

- `POST /api/v1/auth/email/verification`：请求受邀邮箱验证码；对不存在账户返回不可区分的通用成功形态，降低账户枚举风险。
- `POST /api/v1/auth/email/login`：验证验证码、换取 token，并完成唯一的本地邀请映射。
- `POST /api/v1/auth/token/refresh`：轮换 token 并复核本地账户仍然有效。
- `GET /api/v1/auth/me`：返回当前本地用户和 tenant，不返回 CloudBase group 作为业务权限。
- `POST /api/v1/auth/logout`：撤销 Provider 会话并追加本地审计。
- 无效或过期 token 返回统一 `401 unauthorized`；有效 CloudBase 身份但无唯一有效本地邀请返回 `403 invitation_required`；Provider 故障返回 `503 authentication_unavailable`。错误信息不得泄露其他用户、tenant、基金或私有记录是否存在。

## 安全与测试契约

必须验证：

1. CloudBase 模式下伪造 Demo Header 无效；
2. subject 唯一绑定，跨 tenant 重复邮箱失败关闭；
3. inactive 用户或 tenant 不能登录；
4. CloudBase group 不改变本地角色，四类用户继续由本地授权得到不同结果；
5. 访问和刷新 token 只存在于服务端 `HttpOnly` Cookie，不进入 URL、前端 JavaScript、数据库、日志或 Git；
6. 刷新 token 轮换、退出和 Provider 失败有确定结果；
7. 认证审计按用户和 tenant 做 RLS，且应用角色不能更新或删除；
8. 个人、基金、跨租户、审核、来源监测和共享事实路径全部回归；
9. 同步公司查询仍不调用天眼查、搜索或模型，四个业务安全开关不因认证接入而开启。

## 兼容、回滚与限制

- 迁移只给 `users` 增加可空身份映射并新增认证审计表；不回填现有用户，不修改角色、基金、投资关系或真实公司数据。
- 应用可回退到 `AUTH_PROVIDER=demo` 仅用于受控本地故障排查；对外环境不得用它绕过认证。数据库结构可向前保留，优先应用回滚，不为回滚清除身份映射或审计。
- CloudBase 控制台中的环境、邮箱登录开关和受邀账户必须由项目负责人合法创建。本仓库不保存 CloudBase 管理密钥，也不自动接受服务条款。
- 官方服务可能要求图片验证码。V1 对 `captcha_required` 失败关闭并提示管理员，不实现绕过或自建验证码代理；若内部实测频繁触发，须另立小范围任务。
- 当前一个本地 `user` 仍绑定一个 tenant；一人加入多个机构留在后续 organization 里程碑。本决策不实现正式注册、密码、社交登录、watchlist、订阅或支付。
