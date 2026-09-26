# M1-Ops-Recovery：发布执行链与一次受控恢复

本轮只完成阶段 A。业务冻结在 `47c4a5ce82731b6b9a03899012c47ff84d2d7abd`，tree `5afb3f90ba31f459c6ed183639862ade38c87017`；API/frontend 和既有 safety-control 不重建。阶段 A 不启动生产应用、不恢复权限、不切换 current/公网、不生成报告。运维 PR 合并也不构成恢复授权。

## 事故结论与边界

第一处事故已经从实际原始脚本恢复并复现：`container.Image == image_config_digest` 在 containerd image store 下把正确的 manifest ID 判成错误。事故后的局部修正接受 config/manifest 二选一，但尚未证明两者属于同一制品，因此本次以完整身份关联替换，不能只扩大 SHA 白名单。

第二次正常 Caddy 切换的远端 stderr 被原脚本的安全停止流程遮蔽。已查有限时间窗 Docker events、现存容器日志和 daemon journal，不能恢复原始异常。**第二次根因仍为 unknown**，不声称为 Caddy、CloudBase、网络或启动速度问题。它不推翻此前通过的报告生命周期验收。不得以找不到历史异常为由无限阻塞；下一阶段须批准一次完整留证复测。

原始事故版、修正版、26 份相关本机脚本、调用链、原始证据及实际主机参数只保存于受限私有归档。Git 只含脱敏代码、虚构回归和本说明；不提交远程地址、数据库内容、登录身份、备份或凭据。

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
| 数据库/任务/开关 | `observe-metadata` 只读事务，0036、普通角色、RLS 数量、任务数、指定 Worker 状态和已审查关闭开关 | 不读取业务正文；normal 权限要求已审查的准确权限/RLS 基线 |

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

## 本地验收与复用

先复现实际事故表达式失败，再验证统一判定。定向回归覆盖 classic/containerd/index、伪造 label/错 image/缺元数据、各入口预期、迟就绪、超时、SSH/JSON/断言/清理失败、配置采用、原子 current/中断/权限/并发、一次试开与证据保存。

可选 `tests/integration/test_ops_release_containers.py` 仅在显式 `M1_OPS_ISOLATED=1`、Mac Docker Desktop 本地 context、已缓存冻结镜像时运行。使用全新虚构 PostgreSQL、内部无外网网络，真实 API/frontend/control/Caddy，验证只读→普通角色、本机正常入口、完整身份和原子指针。测试中的 migration 只初始化新虚构库；生产不迁移。报告/用量/请求前后都是 0，不产生任何报告。合成 browser 完成标记只测指针状态机，不计为生产认证/报告阅读验收。CI 没有私有制品输入时显式 skip，此分支由本地固定镜像证据补足。

业务代码、migration、frontend 和部署配置均未修改。完整既有 Verify 的实际结果以 PR 准确 head 为准；不得为追平不同环境 skip 数重复跑全站测试。

## 下一次集中批准应涵盖

1. 审查该运维 PR/head/package SHA，精确绑定既有三份业务制品、配置 SHA、目标机器、0036、readonly 角色、冻结旧 current、当次 attempt；接受第二历史根因 unknown 后进行**一次**完整留证复测。
2. 重新只读核对，确认既有加密备份有效及必要当次备份范围；不重跑 Gate A/B、migration、语义重算或历史报告重建。
3. 静态入口保持，启动固定 API/frontend，复用既有 healthcheck，显式恢复普通角色；必要私有认证入口仅按单独批准创建并清理。
4. 先完成同一身份函数及 current 同文件系统/权限/锁预检，再批准一次 normal Caddy 切换。逐层留证，公网探针要求连续成功；admin off，用现有 Compose 机制，不临时改业务代码。
5. 负责人真实浏览器登录后，只读取此前两份成功报告和固定历史报告的预期状态。新报告、新请求键、company_report 用量均允许新增 **0**；公司页面潜在 view/receipt 写入须另列，不能默认为“纯 GET 安全”。允许的登录审计/最近登录状态写入必须明确列出，不扩用户/公司范围。
6. 真实公网验收通过、临时路由清理后执行预验证的 current 原子收尾，记录运行版本/current/公网/回执四种状态，完成最终备份、摘要及回执。未实际完成不得写 M1=CLOSED。
7. 任一硬条件失败，保存原始失败→静态维护→停应用/Worker→固定 control restrict→verify；schema 保持0036，不覆盖备份、不降库、不第二次试开。限制验证失败单独报告，不能标记安全完成。

研究 Worker、Provider、模型、Watchlist、自动刷新/发布、MCP、隧道持续关闭。阶段 B 未获批前停在此处。
