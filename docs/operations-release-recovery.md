# M1-Ops-Recovery：发布执行链与一次受控恢复

本轮收口真实 Compose 执行契约，仅提交一个运维修复 PR，并用最终候选进行目标主机纯观察验证。#106 已合并到 `f5a5a09908d8c3334204c4a6a6e365635c51c8d4`，随后阶段 B 在 `compose-frozen` 安全停止。业务仍冻结 `47c4a5ce82731b6b9a03899012c47ff84d2d7abd` / tree `5afb3f90ba31f459c6ed183639862ade38c87017`；三个私有不可变业务/安全镜像均不重建。

生产保持0036、只读应用角色、API/frontend/Worker停止、静态维护及原current。本轮不合并新PR、不生产apply/prepare、不处理旧锁、不登录、不切入口。已结束attempt的首错、持久归属和封存证据保留；M1尚未关闭。

## 事故结论与边界

第一处事故已经从实际原始脚本恢复并复现：`container.Image == image_config_digest` 在 containerd image store 下把正确的 manifest ID 判成错误。事故后的局部修正接受 config/manifest 二选一，但尚未证明两者属于同一制品，因此本次以完整身份关联替换，不能只扩大 SHA 白名单。

第二次正常 Caddy 切换的远端 stderr 被原脚本的安全停止流程遮蔽。已查有限时间窗 Docker events、现存容器日志和 daemon journal，不能恢复原始异常。**第二次根因仍为 unknown**，不声称为 Caddy、CloudBase、网络或启动速度问题。它不推翻此前通过的报告生命周期验收。不得以找不到历史异常为由无限阻塞；下一阶段须批准一次完整留证复测。

原始事故版、修正版、26 份相关本机脚本、调用链、原始证据及实际主机参数只保存于受限私有归档。Git 只含脱敏代码、虚构回归和本说明；不提交远程地址、数据库内容、登录身份、备份或凭据。

## Compose 2.40 的真实执行契约

阶段B首错是 `metadata.py` 未选profile却直接索引 `safe-degrade-control`。生产2.40.3默认config裁剪未启用服务；Compose退出0、检查器退出2、SSH包装退出0分别记录。它不解释第二次历史unknown，也不涉及业务报告或迁移失败。

`compose.py` 的有限 `ComposeSpec` 同时生成配置观察与实际动作。工作目录始终是明确的业务发布目录；project、env来源及文件顺序固定，不从current或另一条命令截取参数。正常视图不选profile；控制config局部显式选择safe-control和safe-restore。restore动作只选择safe-restore；静态动作使用safe overlay、静态overlay及明确proxy目标。所有子进程去除偶然的COMPOSE_PROFILES/FILE/PROJECT_NAME/ENV_FILES，不改变操作员全局环境。

| 视图 | 文件顺序与profile | 必检 |
| --- | --- | --- |
| normal | production → single-host；无profile | api/frontend固定image、关闭开关、冻结配置SHA；无需可选控制服务 |
| control config | production → single-host → safe；safe-control + safe-restore | 两控制服务固定image、单独profile、read_only、cap_drop、no-new-privileges、restart、network和命令 |
| restore action | 同safe文件；safe-restore | 仅run指定restore服务，不隐式依赖/拉取 |
| static action | production → single-host → safe → static；safe-control | 仅up proxy；停止应用/Worker；明确restrict/verify |

正确profile下缺服务返回BLOCKED；命令非零、坏JSON、缺判断字段返回CHECK_ERROR，超时INCONCLUSIVE。expected记录白名单契约；actual仅服务名、镜像、文件摘要、安全属性、关闭开关及版本，完整展开和env不离开进程。

## 唯一身份判定

`python -m scripts.release_ops` 使用同一 `verify_identity()`：

1. 以 registry 原始响应字节计算摘要；禁止重新序列化 JSON 后计算“等价摘要”。
2. 若为 index，严格选择唯一 linux/amd64 子 manifest；验证摘要、大小、媒体类型。
3. 验证 manifest→config、平台、revision、layer descriptors 与 rootfs diff IDs。
4. 解析实际 Docker context/store、按精确不可变引用和容器实际 Image 分别 inspect。
5. classic store 要求 config ID；containerd 要求 Descriptor 落在已验证的 root/amd64 manifest 链上，媒体类型一致。RepoDigests、RootFS、平台和容器 Config.Image 均须匹配，revision label 单独不够。
6. API/frontend 必须证明到真实容器；未运行的 control 仅允许显式 `daemon_image` 范围，不能伪造容器证明。它沿用同一 registry 判定，不使用旧业务函数验证报告。

缺字段、未知存储或缺原始字节为 INCONCLUSIVE；不匹配为 BLOCKED。layer 压缩内容不重复下载；这里验证原始描述符/config 的内容地址关系与 daemon 的解压层链，镜像拉取层校验由 Docker 完成。不是供应链签名认证。

`prepare-current` 在开公网前再次调用这个函数，要求 API/frontend 两份证明及正确业务 SHA。`apply-current` 只校验已冻结准备对象、当次授权和最终公网验收，执行同文件系统原子换链及读回；不再加入镜像比较公式。

## 检查入口与证据

| 层次 | 入口/预期 | 不能替代什么 |
| --- | --- | --- |
| API 容器 | `observe-health` 复用部署既有 Healthcheck，定义须精确一致、连续 healthy；独立 `/ready` 要求 ready/database reachable | 不证明 frontend/公网 |
| frontend 容器/隔离网络 | 既有 Healthcheck；`/login` 200、HTML 和固定“登录”标记 | 不证明 CloudBase 登录成功 |
| normal Caddy | `/login` 200，预期 HTML，不能是维护响应 | 不用公网 `/health` 当 API 健康证明；正常 Caddy 只反代 frontend |
| safe-degrade | `/health` 200 且 maintenance_read_only | 仅证明降级，不视作网站就绪 |
| 完全静态入口 | `/login` 503 且明确 maintenance_read_only | 不代表应用失败 |
| 匿名拒绝 | 预期 401 或同站登录重定向；不跟随重定向 | 不读取 Cookie、不生成报告 |
| 配置候选/实际采用 | `config-normal` 验候选 SHA；`config-static`/`config-normal-active` 验宿主文件、容器内 hash、只读挂载、进程参数、启动晚于文件 | 文件改了不等于进程已采用；admin off，不调用 reload 管理 API |
| Compose 实际解析 | `compose-frozen` 验 normal/safe 两组实际 image、normal 关闭开关及冻结文件 SHA；完整展开只在内存，不输出 Secret | 停止中容器正确不能证明下一次启动的配置正确 |
| 数据库/任务/开关 | `observe-metadata` 只读事务，0036、普通角色、RLS 数量、任务数、指定 Worker 状态和已审查关闭开关 | 不读取业务正文；normal 权限要求已审查的准确权限/RLS 基线，且保留五项特权关闭、无自有表/角色继承、迁移表禁写及四张关键表 RLS |

每个 JSON 证据包含 attempt/checkpoint、观察位置、容器 ID（不适用时 null）、UTC 开始/结束、耗时、预期/实际、命令类别、退出码与 SSH 远端退出码、脱敏异常、HTTP/传输分类、Location 去查询/片段、证据路径与 SHA。HTTP body 只记录大小、摘要和规则结果。SSH 255 的远端退出码未知，不能伪造 0。第一个失败和清理步骤分别保留，后者不覆盖前者；存储失败也保留原观察并返回 CHECK_ERROR。

HTTP 监督进程约束整个请求（包括 DNS/慢响应体），不是只给 socket 读操作超时。默认总计 60 秒、单次最多 3 秒、最多 12 次、连续成功 2 次；允许批准范围内设置总计不超过 120 秒/30 次。只对 NOT_READY 等待；身份/证书/配置漂移、解析错误不循环试开。每次探针及最终连续成功汇总单独落盘。

| 状态 | 处理 |
| --- | --- |
| PASS | 进入下一已授权步骤 |
| NOT_READY | 有界等待，维护入口保持；耗尽后停止，不反复试开 |
| BLOCKED | 真正硬阻断；按批准的静态维护→停应用/Worker→restrict→verify 隔离 |
| CHECK_ERROR | 检查器错误；保存原始脱敏异常，暂停修核，不能宣称业务失败 |
| INCONCLUSIVE | 证据不足；暂停补证，不凭猜测放行 |

公网已经打开后，安全所需检查不能确认时回到维护。只有负责人**额外明确批准**的非安全归档异常才允许“服务已验证、回执待补”；本轮没有这项批准。`observe` 和 `plan` 不停服务，所有应用变更只能进入另行授权的 `apply`；isolate 的 restrict/verify 失败不得写成“角色已只读”。

## 运维包与执行接口

运维 revision 是此 PR 的准确 commit，与业务 SHA 分列。`ops_package_sha256()` 是排序的 `scripts/release_ops/*.py` 文件名→内容 SHA 映射的规范 JSON 摘要（v1），用于拒绝运行时换代码；交付另记录传输归档 SHA。不把新运维 commit 当成业务制品版本。

以下是接口形状，**不是阶段 A 的生产执行指令**。下一阶段参数文件需在私有 Package 中冻结并获批，不能直接套用占位符：

```sh
python -m scripts.release_ops observe-daemon --input daemon.json --attempt ATTEMPT --evidence EVIDENCE
python -m scripts.release_ops observe-identity --input identity-api.json --blobs BLOBS --checkpoint identity-api --attempt ATTEMPT --evidence EVIDENCE
python -m scripts.release_ops observe-health --input api-health.json --checkpoint api-ready --attempt ATTEMPT --evidence EVIDENCE
python -m scripts.release_ops observe-http --input normal-http.json --checkpoint normal-public --attempt ATTEMPT --evidence EVIDENCE
python -m scripts.release_ops observe-metadata --input schema.json --checkpoint schema --attempt ATTEMPT --evidence EVIDENCE
python -m scripts.release_ops prepare-current --input prepare.json --blobs BLOBS --attempt ATTEMPT --evidence EVIDENCE
python -m scripts.release_ops apply-step --input action.json --authorization APPROVAL.json --attempt ATTEMPT --evidence EVIDENCE
python -m scripts.release_ops apply-current --input commit.json --authorization APPROVAL.json --attempt ATTEMPT --evidence EVIDENCE
```

- 每次观察使用独立 checkpoint 文件；重采集用新证据目录，不覆盖历史文件。action 引用准确 gate 文件 SHA、同 attempt 且 300 秒内的 PASS。CLI `--checkpoint` 是门禁名，不能把 metadata kind 换名冒充其他检查。
- identity 输入为 `{approved, observed}`；approved 含 role/reference/config_digest/platform/revision。daemon 观察通过 SSH 在目标主机执行，仅收集白名单字段；原始 manifests/configs 单独传递。不能拿 Mac daemon metadata 代替生产。
- health 输入含 container/expected_test/bounds；HTTP 输入含 observer_location/contract/bounds；metadata 输入含 kind 及私有主机/数据库名称、预期计数或配置路径/SHA。参数不含密钥，不把完整 inspect/env/Compose 展开打印到日志。
- action 输入含 binding/action/gates；authorization 精确绑定 attempt、business_sha、ops_package_sha256、root、allowed_actions。typed actions 只用固定 releases/SHA、部署现有 Compose 顺序、`--no-deps --no-build --pull never`；无迁移/镜像构建/报告/Worker 启动命令。
- `prepare-current` 有临时链接演练，属于 apply 前准备，**不应称为远程只读操作**；本阶段只在本机临时目录执行。目标必须位于同文件系统 releases 内，无逃逸链接；演练权限/替换/读回后保存 prepared SHA。
- `apply-current` 的 prepared SHA、公网既有报告只读验收和临时路由清理证明必须另列到批准记录。重复提交同一目标可读回确认；中断前后有回归覆盖。公开状态、运行版本、current、回执状态分别记录。
- 共享 `flock` 和持久 `.release.lock` attempt 阻止并发/不同尝试；`.opened-ATTEMPT.json` 在首次正常入口操作前独占写入，即使命令失败也不自动允许第二次试开。失败后保留锁/证据，只有负责人批准新 attempt 才可人工归档并解锁，禁止自动删锁重试。

## 真实回归与隔离执行链

普通Verify在RUNNER_TEMP安装官方Compose2.40.3独立二进制并验证固定SHA，不替换宿主插件。`test_ops_compose_contract.py` 调用正式 `python -m scripts.release_ops observe-metadata`，真实解析冻结Compose和虚构env；不需要Docker daemon/GHCR凭据，CI缺二进制直接失败。测试记录实际调用，拒绝单profile/缺服务/错镜像/开关开启/缺环境或字段/命令失败/坏JSON，证明全程只有version/config。旧总含全部服务的fixture不再代表profile行为。

`test_ops_release_compose.py` 另须显式M1_OPS_ISOLATED=1、本机unix Docker、已缓存三个冻结amd64镜像及原始registry字节。实际经归档安装、正式CLI、Compose、apply-step/prepare-current/apply-current，贯通静态0036只读→启动应用→显式恢复角色→prepared序列化读回→正常代理→实际探针→原子current；再注入真实契约拒绝，验证实际停止/限权、首错不变及第二次正常入口拒绝。42张虚构业务表完整内容摘要不变，报告/用量/映射零新增。browser完成标记明确SIMULATED，不代表CloudBase或生产报告验收；私有镜像分支不在普通CI运行。

隔离参数仅允许独立root/project、虚构env、loopback端口、关闭NAT的独立bridge网络（internal=true在本机Docker会取消已请求的端口发布）及amd64平台。测试覆盖文件固定为`deploy/compose.ops-isolated.yml`且摘要绑定；不接受业务command/image/env替换，拒绝远端Docker。生产不使用此映射，也不改被冻结配置。

## 审查过的执行适配器

`bridge.py` 用同一源码完成Mac→SSH→明确Python→独立归档→正式CLI；输入只有声明式参数，不能注入任意远端Shell。archive成员只允许已摘要批准的ops模块，拒绝链接/重复/覆盖；安装前后核对归档及内容SHA。解释器以-I启动，固定模块引导器只选择核验过的scripts包，不借用仓库cwd/PYTHONPATH，也不会被operator.py同名文件遮蔽。引导器仍进入正式CLI解析、Recorder和apply逻辑，不直接调用prepare/commit。

Mac端使用已审查的 `python -m scripts.release_ops.bridge --request PRIVATE_REQUEST.json --destination APPROVED_HOST --interpreter /usr/bin/python3 --output ENVELOPE.json`。当前仅安装新的独立候选目录及观察；request不携带生产apply授权。远端-I引导正式模块CLI，输出记录具体解释器、工作目录、包内容SHA、bridge源码SHA及formal argv。Docker Desktop挂载别名只在已核验的本机隔离root/project中允许，生产仍精确匹配Source。

本轮本机定向74项通过；冻结制品同路径演练1项通过（42张虚构表摘要不变，一次正常入口，故障后真实限权/停止）。普通完整Verify及最终现场只读证据均须绑定提交后的准确head，不能以该本机数量代替CI。

SSH传输/远端包装/检查器退出码独立；包装0不能覆盖检查器失败。`host.py` 只读采集停止态、实际Cmd/Entrypoint/Healthcheck、角色/RLS、current和锁；不假设frontend继承入口为null，也不把systemd解释器二进制当脚本读取。实际下一次执行不再依赖旧私有可执行适配器；私有参数和业务验收对象仍需集中批准。

最终候选可以安装至新的独立观察目录，运行compose-frozen、schema/role-readonly/jobs-off/config-static/维护及同一identity。观察不写锁/current，不启动容器，不可复用为未来恢复的新鲜门槛。准确head/tree/Verify、包及现场证据只以统一私有 `M1 Operations Execution Contract & Resume Readiness` 为准。

## 下一次集中批准应涵盖

1. 审查该运维 PR/head/package SHA，精确绑定既有三份业务制品、配置 SHA、目标机器、0036、readonly 角色、冻结旧 current、当次 attempt；接受第二历史根因 unknown 后进行**一次**完整留证复测。
2. 重新只读核对，确认既有加密备份有效及必要当次备份范围；不重跑 Gate A/B、migration、语义重算或历史报告重建。
3. 静态入口保持，启动固定 API/frontend，复用既有 healthcheck，显式恢复普通角色；必要私有认证入口仅按单独批准创建并清理。
4. 先完成同一身份函数及 current 同文件系统/权限/锁预检，再批准一次 normal Caddy 切换。逐层留证，公网探针要求连续成功；admin off，用现有 Compose 机制，不临时改业务代码。
5. 负责人真实浏览器登录后，只读取此前两份成功报告和固定历史报告的预期状态。新报告、新请求键、company_report 用量均允许新增 **0**；公司页面潜在 view/receipt 写入须另列，不能默认为“纯 GET 安全”。允许的登录审计/最近登录状态写入必须明确列出，不扩用户/公司范围。
6. 真实公网验收通过、临时路由清理后执行预验证的 current 原子收尾，记录运行版本/current/公网/回执四种状态，完成最终备份、摘要及回执。未实际完成不得写 M1=CLOSED。
7. 任一硬条件失败，保存原始失败→静态维护→停应用/Worker→固定 control restrict→verify；schema 保持0036，不覆盖备份、不降库、不第二次试开。限制验证失败单独报告，不能标记安全完成。

研究 Worker、Provider、模型、Watchlist、自动刷新/发布、MCP、隧道持续关闭。旧阶段B授权已经结束。新PR/head/包经复审后，须另批新attempt及旧持久锁处理：先确认无活动flock持锁进程，再按批准方式归档归属，不无条件rm锁。本轮认证0；将来登录/刷新各最多2次及指定用户last_login_at/updated_at伴随写入，仅在新集中授权中有效。
