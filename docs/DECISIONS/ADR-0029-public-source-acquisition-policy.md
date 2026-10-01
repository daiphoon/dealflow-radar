# ADR-0029：公开 HTTP 获取政策与访问控制分离

- 状态：本地工程候选，待 PR 审查；不代表生产启用。
- 日期：2026-09-30
- 范围：M2-F 有界公开来源获取及审计；不增加 Provider、预算或自动发布权限。

## 决定

保留默认 `WEB_RESEARCH_ROBOTS_MODE=enforce`。仅当政策明确配置
`advisory_public_http` 且实际调用标注 `PUBLIC_STANDARD_HTTP` 时，robots 的
Disallow、缺失、普通不可达和格式异常作为观测，不单独阻止无需认证的有界公开 GET。
策略版本为 `public-http-robots-advisory-v1`，与来源许可和正文访问结果分别记录。

这是负责人批准的自定义获取政策，不宣称符合 RFC 9309 的默认执行规则。
[RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) 将 robots 与访问授权区分，
同时规定解析规则和不可达时的处理；本决定没有把标准解释为任意抓取许可。
现有 Python RobotFileParser 的匹配语义继续使用，匹配行只解释其实际判断，
不宣称实现了完整 RFC 规则优先级。未显式选择该路线的监测继续 enforce。

## 安全与计量

- 登录、Cookie/session、会员或付费墙、验证码、WAF、401/403/429 均停止。
- DNS、逐跳 URL、连接 peer、TLS、取消、请求/字节/时间硬帽保持；不调用渲染器绕过。
- robots 请求、重试、重定向属于同一任务预算；耗尽后正文不得继续 dispatch。
- robots 观测记录 UA、时间、缓存、规则摘要、匹配行、响应/错误及政策决定。
- 成功正文记录路线、技术访问状态、正文/辅助字节、总请求和 elapsed；失败 coverage
  保留路线与 robots 观测，并区分辅助 HTTP 与目标正文实际 dispatch。
- 页面提及安全技术本身不是 CAPTCHA；明确交互挑战才拒绝。来源许可未改变。

## 分层与后置

公开 API/RSS/Atom/PDF 复用已有解析。公开 JS 壳仅标记
`dynamic_rendering_required`；未来 rendered 接口只能接收通过技术访问检查的公开页面，
仍须独立授权和计量。本轮无 Jina、浏览器抓取或新 Reader。
自动读取受限时只保留 UNAVAILABLE 或授权人工导入，不升级为已确认事实。

## 验收与回退

实际 TrustedSourceFetcher 的 MockTransport 矩阵涵盖 robots、正文访问、重定向、
peer/DNS、Cookie、预算、PDF/RSS/Atom 和 JS 壳。fixture 成功不表示旧 URL 已恢复可读。
回退只恢复 `enforce` 配置；不改历史证据、许可、schema 或报告。
