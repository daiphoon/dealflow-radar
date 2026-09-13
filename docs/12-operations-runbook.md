# 12 运维手册

当前已有可本地运行的 Demo、已验收的 M5A 生产构件和腾讯云中国香港 M5B 邀请测试环境。香港环境已完成 HTTPS、端口收口、四类账户、主机重启、COS 加密备份下载与隔离恢复；本手册继续保留可重复部署和告警接线步骤。

## E3 关注公司低频检查（默认关闭）

本节为操作说明，不表示已安装定时任务或已获真实运行授权；完整范围见 [E3 固定切片](15-incremental-event-delivery-plan.md#e3-固定切片与停止边界)。需先在目标环境按常规迁移流程应用 `0031`，配置活跃平台管理员的 `WEB_RESEARCH_WORKER_USER_ID`、`WORKER_TENANT_ID` 及非 owner 数据库角色。

- 只读预览：`.venv/bin/python -m scripts.queue_watchlist_checks`。列出到期公司、两家现有搜索 Provider、次数上限和计划费用上界；不开 Provider、不改任务。未知费用不当成零。
- 实际入队入口：同一命令加 `--enqueue`，仅当 `WATCHLIST_MONITOR_ENABLED=true` 时生效，每批默认最多 5 家。没有创建或安装实际 Cron；将来受控启用时可由既有 Cron 定期触发这个短命令，重复触发受公司锁、唯一活动任务和到期状态控制。
- 调用仍由 `scripts.run_web_research_worker` 完成，沿用原四个调用闸门及 E2 预算；`AUTO_REFRESH_ENABLED`、自动发布、模型解读和旧可信来源开关保持关闭。不能仅开关注开关就绕过调用或预算限制。
- 停用时关闭专用开关，停止调度入口和当前 Worker，再以关闭后的配置重启；编辑 `.env` 不会修改已有进程的环境。应用内取消、取消全部关注或账号停用会在下一次请求派发前生效，已在途请求的支出仍保留。
- 预算不足、失败或取消保留旧成功时间和结果；不确定支出沿用 E2 回执核对，不通过重置累计费用绕过。新计划默认 7 天，失败指数退避、连续无新文档降频，参数见 `.env.example`。关注页显示计划进度，不能将其解释为经营状况或完整事实覆盖。
- 回滚优先关闭开关并保留历史；有巡检历史时 `0031` 拒绝破坏性降级。真实定时器、服务器部署和低频命中率验证属于后续受控操作。

## 1. 部署前检查

1. M5B 使用腾讯云中国香港云服务器；不使用家庭 Ubuntu 服务器。外部用户不依赖 Tailscale，管理员可以使用 Tailscale SSH。
2. 从 `.env.example` 创建部署 Secret，确认外部调用、付费调用、自动刷新、自动发布和审核工作台等安全开关仍为关闭，禁止提交 `.env`。
3. 检查镜像版本、PostgreSQL 持久卷、TLS、允许来源、备份目标和磁盘余量。
4. 先执行迁移备份与 `alembic upgrade` 演练，再部署 API/Worker/Cron。
5. 用两个租户/基金的虚构数据运行权限冒烟测试。
6. 外部 Provider 逐个完成许可、价格、预算和 `dry-run` 审批后才启用。
7. 对外环境必须使用 `AUTH_PROVIDER=cloudbase`；`AUTH_PROVIDER=demo` 和 `X-Demo-User-Id` 只允许本机/CI。

Docker Compose 文件只依赖标准容器、环境变量和卷，可迁移到不同主机；Caddy 在真实域名环境自动申请和续期 HTTPS 证书，不绑定云厂商 SDK。CloudBase 仍只负责身份认证，PostgreSQL、tenant、基金、RLS 和业务权限不迁移到 CloudBase。

### M5A 零云费用生产式验收

M5A 只在本机使用虚构数据验证生产构件，不购买服务器、不创建云数据库、不申请证书，也不对局域网或互联网开放。验收覆盖生产镜像、配置失败关闭、迁移、受限数据库账户、备份恢复、数据库故障和登录页；它不能替代 M5B 的香港真实部署验收。

```bash
m5() {
  docker compose \
    --env-file deploy/acceptance.env.example \
    -f compose.production.yml \
    -f deploy/compose.acceptance.yml "$@"
}

m5 config -q
m5 build api frontend
m5 up -d acceptance-db restore-db
m5 run --rm --no-deps preflight
m5 run --rm migrate
m5 run --rm migrate python -m scripts.seed_demo
m5 run --rm bootstrap-role
m5 up -d api frontend proxy
m5 ps
```

验收环境仅绑定 `127.0.0.1`，统一访问 `http://localhost:3100/login`。`/health` 只证明进程存活，`/ready` 还会实际执行数据库查询；停止测试数据库时应分别返回 `200` 和 `503`，数据库恢复后 `/ready` 应恢复为 `200`。容器内后端使用 `app`、前端使用 `node`，API 和前端端口不直接发布，只有 Caddy 入口可被本机访问。正式模式必须拒绝伪造的 `X-Demo-User-Id`。

数据库备份采用 PostgreSQL 自定义格式并生成 SHA-256，恢复命令只接受 `/backups` 下的纯文件名，且目标数据库名称必须以 `_restore_test` 结尾：

```bash
m5 run --rm backup
backup_path="$(ls -t backups/m5a-acceptance/*.dump | head -n 1)"
export BACKUP_FILE="${backup_path##*/}"
m5 run --rm restore-test
unset BACKUP_FILE
```

必须再核对源库与隔离恢复库的 Alembic 版本及关键表数量。上述备份含虚构数据、保存在本机且未加密，只用于 M5A 工具验收；不得把这种做法照搬到真实数据环境。验收结束可用下面命令删除一次性容器和卷，该命令禁止用于真实部署：

```bash
m5 down -v --remove-orphans
unset -f m5
```

### M5B 香港环境部署顺序（已落地，保留作重建手册）

当前架构是腾讯云中国香港服务器、同机 PostgreSQL、CloudBase 邀请认证和客户端加密 COS 备份。入口为 `app.dealflowradar.cn`；香港资源不要求也不能用于大陆 ICP 备案，不接入中国大陆 CDN。该环境只服务邀请制 MVP，未来迁入大陆和收费前仍须成立并确认经营主体、完成企业备案和合规审查。

服务器套餐、带宽、期限、价格和 COS 地域仍由项目负责人确认。重建时按以下顺序执行：

1. 购买腾讯云中国香港服务器并固定公网地址；配置云安全组，只允许公网 `80/443`，SSH 仅走 Tailscale 或明确管理地址；创建私有 COS 桶、14 个每日加 8 个每周恢复点的生命周期策略；服务器 COSCLI 凭据仅允许上传指定前缀，恢复由管理终端上独立获读取授权的身份执行；
2. 复制 `deploy/single-host.env.example` 为 Git 忽略的 `deploy/single-host.env`，填写真实值并执行 `chmod 600 deploy/single-host.env`；不得把 Secret 放入命令历史、镜像、日志或 Git；
3. 为 `BACKEND_IMAGE` 和 `FRONTEND_IMAGE` 使用固定提交对应的不可变标签，不使用漂移的 `latest`；
4. 运行无网络、无数据库写入的 `preflight`，确认生产模式、CloudBase、PostgreSQL、HTTPS 和八个初始安全开关；
5. 升级前创建数据库与许可范围内文件资产的异机加密备份，并在隔离数据库实际恢复；
6. 用迁移账户执行 `migrate`，再用 `bootstrap-role` 收敛无超级用户、无 `BYPASSRLS` 的应用账户；API 永远使用受限应用账户；
7. 启动 API、前端和 Caddy，验证真实证书、防火墙、公网端口、`/health`、`/ready`、日志轮转、磁盘/服务告警和主机重启恢复；
8. 用个人、基金、其他租户和平台管理员四类受邀 CloudBase 账户执行浏览器和 RLS 负向验收；再从中国大陆至少两种运营商网络验证登录、查询与详情访问，全部通过后才可进入 M6。

单机方案使用专用环境文件和 Compose 覆盖。先复制示例并替换全部占位值，三个数据库密码必须彼此不同；写入数据库 URL 的密码如含保留字符，必须进行百分号编码：

```bash
prod() {
  docker compose \
    --env-file deploy/single-host.env \
    -f compose.production.yml \
    -f deploy/compose.single-host.yml "$@"
}

prod config -q
prod build api frontend backup
prod run --rm --no-deps preflight
prod run --rm backup
prod run --rm migrate
prod run --rm bootstrap-role
prod up -d api frontend proxy
prod ps
```

`bootstrap-role` 默认只授予业务表查询、新增和修改权限；删除权限仅单独授予 `personal_watchlist_items`，并继续由 PostgreSQL RLS 限制为当前用户自己的关注记录。部署包含应用角色权限修正的版本时，必须在启动 API 前重新执行 `prod run --rm bootstrap-role`；不得为解决取消关注失败而对所有业务表统一授予 `DELETE`。

`age` 私钥必须在独立管理终端生成并离线备份，只把公钥形式的 recipient 写入 `BACKUP_AGE_RECIPIENT`。`BACKUP_AGE_IDENTITY_PATH` 只在隔离恢复时临时指向私钥文件；生产服务器日常运行不得保存该文件。备份成功后目录中只应出现 `.dump.age`、`.sha256` 和 `.plain.sha256`，不能留下 `.dump`：

```bash
prod run --rm backup
export BACKUP_DIR='./backups/production'
export BACKUP_FILE='替换为实际的.dump.age文件名'
export COSCLI_CONFIG_PATH='./deploy/coscli.secret'
export COS_BUCKET_ALIAS='替换为私有桶别名'
export COS_BACKUP_PREFIX='dealflow-radar/postgres'
./deploy/upload-backup-cos.sh
unset BACKUP_DIR BACKUP_FILE COSCLI_CONFIG_PATH COS_BUCKET_ALIAS COS_BACKUP_PREFIX
```

COSCLI 配置必须通过官方工具交互生成并执行 `chmod 600`，不要把 Secret ID 或 Secret Key 放进上述命令。上传脚本不会删除本地文件，也不会上传解密私钥；实际 COS 创建前只能用假命令测试，不能宣称异机备份完成。

香港环境的服务器账号 `dealflow-radar-backup-writer` 使用 `DealflowRadarCosBackupUploadOnly`：仅允许向既有备份前缀执行 `cos:PutObject`。该账号不能下载、列举或删除；用它执行下载时，`HEAD Object` / `GET Object` 返回 403 属于预期权限边界，不能据此认定备份损坏或补授生产读取权限。COSCLI 下载需要的读取动作见[腾讯云官方说明](https://intl.cloud.tencent.com/zh/document/product/436/43256)。

恢复使用管理终端上已获读取授权的管理身份：登录 COS 控制台，在 `dealflow-radar/postgres/` 选取明确恢复点，下载同名 `.dump.age`、`.dump.age.sha256` 和 `.dump.age.plain.sha256` 三个文件；将它们放入 Git 忽略的私有目录，校验加密文件及解密后的哈希。服务器上的上传凭据和 Mac 上的解密私钥分别保管；临时签名下载链接不写入聊天、Git 或操作日志。核对恢复来源必须是 COS 实际下载，不能用服务器 SSH 副本代替这项验收。

在管理终端的独立 Compose 项目或一次性容器中创建 `_restore_test` 数据库，临时只读挂载离线私钥，使用 `--profile restore` 启动独立恢复库；以下命令只用于已配置的隔离恢复环境：

```bash
export BACKUP_FILE='替换为从COS下载的.dump.age文件名'
prod --profile restore run --rm restore-test
unset BACKUP_FILE
```

升级失败时停止新版本容器，保留备份和日志，优先把两个镜像标签切回上一已验证提交，再运行 `prod up -d api frontend proxy`。不得为了快速回滚直接执行 Alembic downgrade；已存在真实或跨作用域数据时，降级可能丢失表或改写去重键。数据库结构不向前兼容时必须停止并另行评估，不能伪造迁移成功。真实停服只执行 `prod down`，不得带 `-v`。

### M5B 告警和大陆网络拨测

腾讯云可观测平台已经为香港轻量应用服务器启用系统盘利用率告警：超过 75% 时通过系统预设模板向 1 个接收人发送邮件和短信；账户原有的流量包余量告警保持启用。云拨测当前是 15 天免费试用，任务 `dealflow-radar-香港邀请测试-登录页` 每 5 分钟从上海电信、广州移动和北京联通三个 LastMile 节点访问登录页。首批 4 次观测全部标记正常，整体性能为 717—36,983 ms；其中北京联通出现一次 36,983 ms 的慢样本，说明“可访问”不等于“已经证明长期稳定”，M6 应继续观察分位数和失败率。试用到期会停止，未经项目负责人再次确认不得升级专家版或购买套餐。

应用健康检查、数据库加密备份和 COS 上传失败使用独立飞书机器人通知。Webhook 是 Secret，只保存在服务器 `0600` 文件中；消息只包含环境、主机、失败的 systemd 单元和时间，不含用户、公司或业务正文。同一失败单元每小时最多触发两次通知，避免连续故障刷屏。

合并本交付后，在服务器安装脚本和 systemd 接线：

```bash
cd /opt/dealflow-radar/current
sudo install -o root -g root -m 0555 deploy/notify-feishu.sh \
  /usr/local/sbin/dealflow-radar-notify-feishu
sudo install -o root -g root -m 0644 deploy/systemd/dealflow-radar-alert@.service \
  /etc/systemd/system/dealflow-radar-alert@.service
for unit in \
  dealflow-radar-health-check.service \
  dealflow-radar-backup.service \
  dealflow-radar-backup-upload@.service; do
  sudo install -d -o root -g root -m 0755 "/etc/systemd/system/${unit}.d"
  sudo install -o root -g root -m 0644 deploy/systemd/alert-on-failure.conf \
    "/etc/systemd/system/${unit}.d/alert.conf"
done
sudo systemctl daemon-reload
```

先从 `deploy/ops-alert.env.example` 核对字段，再使用 `sudoedit /opt/dealflow-radar/shared/ops-alert.env` 安全写入飞书自定义机器人 Webhook，并执行 `sudo chmod 600`；不得把完整 URL 放进 shell 历史、Git 或聊天记录。未写入 Secret 前可执行本地 dry-run；写入后直接启动通知单元验证送达，不需要人为破坏 API 或备份：

```bash
OPS_ALERT_DRY_RUN=true \
  /usr/local/sbin/dealflow-radar-notify-feishu dealflow-radar-health-check.service
sudo systemctl start \
  dealflow-radar-alert@dealflow-radar-health-check.service.service
sudo journalctl -u 'dealflow-radar-alert@*' --since today --no-pager
```

飞书测试成功后核对三类源单元的 `OnFailure` 均非空。若通知脚本失败，源健康检查或备份仍保持原失败状态，详细原因留在 systemd journal；不得把 Webhook 打印到排障输出。

## 2. 日常健康指标

监控 API 错误/延迟、数据库连接和存储余量、任务队列年龄、租约过期、Provider 失败率、无变化任务占比、缓存命中率、LLM Schema 成功率、身份例外量、未确认高风险线索量、预算消耗和备份结果。同步查询外部调用数应恒为 0。

## 3. 安全的更新操作

更新前先运行 `dry-run`，核对公司、原因、Provider、预计搜索/Token/商业数据调用和费用。只在变更单明确授权的范围内开启 `EXTERNAL_CALLS_ENABLED`；付费能力还需 `PAID_API_CALLS_ENABLED` 与正预算。任务完成后立即核对 `usage_ledger` 和有效产出，不长期保留调试日志正文。

### CloudBase 邀请制身份认证 V1

CloudBase 只接入身份认证，不创建或迁移数据库、云函数、业务权限、tenant、基金或 RLS。第一次真实验收需要项目负责人在 CloudBase 控制台完成以下账户操作，本项目不得代为接受条款或创建付费资源：

1. 创建或选择一个合法持有的 CloudBase 环境，开启邮箱登录；
2. 预先创建四个受邀邮箱账户，分别对应无基金个人、基金用户、其他 tenant 用户和平台管理员；
3. 确认本地 `users` 中存在相同邮箱的唯一 active 记录，且对应 tenant 为 active；不得用同一邮箱跨 tenant 建两个待绑定用户；
4. 不把 CloudBase group 当作本地角色，不在 CloudBase 迁移基金或公司权限。

数据库先备份并升级到当前 Alembic head（2026-09-10 已部署基线为 `0031`；`0015` 只是 CloudBase 认证首次落地时的历史基线）。后端与前端使用相同的服务端环境配置；环境 ID 和客户端 ID 不是业务权限凭证，但仍应由部署配置管理，不写死在代码：

```bash
export AUTH_PROVIDER=cloudbase
export CLOUDBASE_ENV_ID='replace_with_cloudbase_env_id'
export CLOUDBASE_CLIENT_ID='replace_with_client_id_or_leave_empty'
export EXTERNAL_CALLS_ENABLED=false
export PAID_API_CALLS_ENABLED=false
export AUTO_REFRESH_ENABLED=false
export AUTO_PUBLISH_ENABLED=false
```

启动后统一访问 `http://localhost:3000/login`；同一次本地验收不要混用 `localhost` 和 `127.0.0.1`，否则浏览器会把两者的 Cookie 隔离。邮箱验证码固定使用 CloudBase `target=USER`，因此未在 CloudBase 创建的账户不会自行注册；本地无唯一邀请时登录也会拒绝。首次绑定只能发生在刚完成验证码交换的登录请求中，普通 Bearer 请求和 token 刷新不能首次绑定。access/refresh token 只能存在于 Next.js 的 `HttpOnly` Cookie，不得复制到命令、URL、日志、截图、数据库或 Git。CloudBase 身份调用是登录基础设施调用，不会打开上述四个业务数据开关，也不会调用任何公司信息 Provider。

至少验证：伪造 Demo Header 无效、四类用户权限符合本地角色和基金授权、无基金用户仍可查共享公司、其他 tenant 看不到私有数据、会话过期可刷新、退出后需要重新登录、`authentication_audit_logs` 有绑定/登录/刷新/退出记录。若出现 `authentication_challenge_required`，说明 CloudBase 要求图片验证码；V1 必须停止，不得绕过，另行评估官方安全挑战接入。

#### 手机号验证码登录 V1（默认关闭）

手机号登录只改善邀请测试的收码体验，不改变授权模型，也不开放注册。CloudBase 使用 `target=USER`，只有已经存在的 CloudBase 账户可以收到登录验证码；应用端进一步要求该登录返回的 `subject` 已绑定本地用户，手机号路径绝不按邮箱做首次绑定。完整手机号只在浏览器提交与 CloudBase 身份请求中短暂出现，不进入业务数据库、验证码 Cookie、URL 或应用日志。

启用前按以下顺序操作：

1. 在 CloudBase 控制台开启短信验证码登录，并核对当前短信额度、单号码限制和可能费用；不得因为本项目开关为 false 就假定供应商不会计数；
2. 选择一个已通过邮箱登录且已绑定本地用户的测试账户，在 CloudBase 用户管理中给该同一账户增加中国大陆手机号；优先使用控制台，避免把手机号写入命令历史；
3. 分别按邮箱和手机号查询 CloudBase 用户，确认 UID 完全一致。若产生两个 UID，立即停止并删除错误关联，不得在本地数据库按邮箱合并；
4. 在受保护的部署环境中同时设置：

```bash
PHONE_LOGIN_ENABLED=true
AUTH_PHONE_CODE_COOLDOWN_SECONDS=60
AUTH_PHONE_DAILY_LIMIT=5
AUTH_PHONE_ENV_DAILY_LIMIT=50
```

5. 重新部署后确认登录页同时出现邮箱和手机入口；用同一账户完成真实短信登录，核对 `/api/v1/auth/me` 返回原本地 `user_id`、tenant 和角色，`authentication_audit_logs` 只新增正常会话事件且没有第二条身份绑定；
6. 核对无基金、基金叠加、其他 tenant 隔离、退出和邮箱回退均不回归；不得把验证码、手机号、Cookie 或 Token 粘贴到聊天、工单、截图或 Git；
7. 验收异常或短信成本不可接受时，把前后端共同使用的 `PHONE_LOGIN_ENABLED` 改回 `false` 并重新部署。关闭入口不需要数据库回退，也不会删除既有邮箱登录能力。

应用限流是邀请规模下的单进程内存保护：默认同号码间隔 60 秒、24 小时 5 次、全环境 24 小时 50 次。服务重启会清空本地计数，CloudBase 自身的持久限额仍是第二道防线。扩展为多个 API 实例或明显增加用户前，必须改成共享、可审计的持久限流；当前不得为未来规模提前引入 Redis。真实短信验收只发送完成测试所需的最少次数，并在 CloudBase 控制台核对实际用量。

故障时优先回退到与现有向前兼容数据库结构匹配的应用版本，不直接执行数据库 downgrade；`0016/0017` 已产生个人关注、申请、查看回执和报告后，降级会删除这些表及数据，必须先备份并单独评估。`AUTH_PROVIDER=demo` 只能作为绑定 `127.0.0.1` 的本地排障手段，不能用于已对外开放的环境。解绑或换绑 subject 不得直接清空字段，应先核对审计并另行执行受控纠错。

### Mock Worker V1（仅 Demo）

先确认数据库中已有 `mock_refresh` 任务，再按虚构租户运行一次：

```bash
export WORKER_TENANT_ID="$(uv run python -c 'from backend.app.demo import ALPHA_TENANT_ID; print(ALPHA_TENANT_ID)')"
APP_MODE=demo uv run python -m scripts.run_mock_worker
```

命令每次最多领取一个任务；无任务返回 `idle`。必须显式设置 `APP_MODE=demo`，并使用代码中固定的两个虚构租户之一；缺失模式、非 Demo 模式和其他租户均在连接数据库前拒绝。该入口不会加载 Provider 或访问网络：有当前快照时模拟无变化检查并保留 `data_as_of`，无快照时保持 `unknown`。运行后核对任务已完成、租约已释放、心跳存在，且 `usage_ledger.external_calls = 0`、`estimated_cost = 0`。它不能代替真实公司信息检查，也不是常驻 Worker 或 Cron。

### 人工研究导入 V1（本机、公开来源）

先确认数据库已迁移到 head、目标公司主数据已存在、操作者是 active 机构管理员。将 JSON 放在 Git 忽略目录后执行：

```bash
mkdir -p data/private/research_imports
cp data/sample/manual_research_import.json data/private/research_imports/manual-example.json
export RESEARCH_IMPORT_FILE=manual-example.json
export IMPORT_USER_ID="$(uv run python -c 'from backend.app.demo import ALPHA_USER_ID; print(ALPHA_USER_ID)')"
RESEARCH_IMPORT_DRY_RUN=true uv run python -m scripts.import_research_json
uv run python -m scripts.import_research_json
```

先核对 `dry-run` 输出的目标公司、策略版本、URL 检查上界、验证尝试上界、零 Token/费用和零数据库写入；因为不读取公司主数据，该数量是身份解析前的保守上界，且该步骤不连接数据库、不访问网络。正式输出只包含批次 ID、状态和路由计数。完成后核对：未解析记录只形成实体提及审核项且不生成事件；安全记录为 `auto_published` 并进入快照；其他记录为 `unconfirmed_lead` 且不创建逐条事件审核项。默认 `EXTERNAL_CALLS_ENABLED=false` 时 URL 未检查，因此所有已解析记录安全降级为未确认，`usage_ledger` 的外部调用、Token 和费用均为 0。只在受控小批次显式开启 URL 检查，并核对已解析记录的 URL 检查数不超过 `SOURCE_URL_MAX_CHECKS_PER_IMPORT`；该检查仍不调用模型或付费 API。重复同一文件应返回 `duplicate`。批次号复用但内容改变、来源代码元数据冲突或外部记录内容冲突时整批失败并回滚，不要绕过去重键手工改库。

### 负责人确认表格导入 E4.1（本地）

此入口独立于上方 JSON 路径，复用现有人工决定与证据表。前提为数据库已迁移至 `0032` 或之后兼容版本、使用非 owner 应用连接，`IMPORT_USER_ID` 是 active 平台管理员，`IMPORT_TENANT_ID` 与该用户一致。不要求官网、政府正文或机构管理员身份；原已核验依据不覆盖。仅接收已获负责人确认的公开资料，机构内部投资、持仓和协议不属于此入口。

原 Excel 保持不变，复制到 Git 忽略的 `data/private/research_imports/`；预览、完整输出和文件哈希也只保存在私有目录。读取第 5 行列名；最大 5 MiB、解压后 32 MiB、每个 sheet 1000 行。公式只作为不支持的数据字段报错，不求值、不执行宏、不访问 URL。全表可预览，正式导入必须显式选一个公司 ID。

以下是命令结构；数据库、两个用户上下文变量、资料文件和确认时间应在本地安全配置。`--confirmed-at` 使用带时区的真实负责人确认时间；`--dataset-key` 为同一资料库的稳定标识，更正版不换标识或原记录 ID。

```bash
uv run --frozen python -m scripts.import_curated_workbook --file reviewed-companies.xlsx --dataset-key owner-reviewed --company-key C001 --mode identity_only --confirmed-at "$CURATED_CONFIRMED_AT" --reason "$CURATED_CONFIRMATION_REASON"
```

默认只读数据库预览：新增、复用、更正、待核、冲突、前后字段、来源、目标作用域及 `preview_hash`；零写入、零外部调用、零 Token/调用费用。复核预览后，在同一条命令增加 `--apply --preview-hash "$CURATED_PREVIEW_HASH"` 才执行。文件、选择、确认说明或数据库内容改变时，旧预览哈希不能套用。

- `identity_only`：仅公司与确认别名，不读取事件、证据来源、待核 sheet 的业务内容。独立发现必须在目标公司没有答案、快照或业务缓存的独立数据库/隔离输入上开展；此选项不会删除数据库中已有答案。
- `initial_data`：公司加初始资料。已复核普通记录通过逐观测人工决定生成共享事件；原件为操作者个人私有层，未确认和严重负面只保存私有线索。原媒体、品牌口径及未知日期保留，来源链接标为未重新检查，风险和重要性不自动评分。
- 同文件/选择重试不新增；稳定记录 ID 的更正追加原件、观测和决定。迟到的旧确认、重复记录 ID、主体和别名冲突定点保留，不能换 ID 绕过去重或覆盖历史。默认不删除原文件遗漏的旧事件。
- 如需绑定原 `in_review / identity_evidence_missing` 申请，在首次预览及正式导入均传相同 `--request-id`。仅关联身份并记录新审计，保持历史失败、进度和累计费用，不创建研究任务。取消或状态变化拒绝绑定；重复已导入批次不会附带新的绑定动作。

本地验收已覆盖原已选单公司，两种用途分库测试。生产导入及部署未在 E4.1 执行；下一工程范围见看板，不能把初始化数量计入自动发现召回。导入事务失败整体回滚；`0032` 存在人工资料或未联网检查的快照时拒绝降级，后续部署须保留备份并采用前向修复，不删除审计来强行回退。

### 业务发现与融资增量 E4.2

新路径由 `WEB_RESEARCH_INCREMENTAL_ENABLED` 控制，默认 `false`；它不替代外部调用、Worker、预算及监控总开关。现有 Worker `--dry-run` 输出是否采用增量路径、查询策略版本及原调用上限，包含已人工准入、等待业务更新的申请；预览仍为零写入/零调用。正式运行须使用迁移至 `0033` 的非 owner 应用连接和有效平台管理员上下文。

开启后的业务步骤使用工商全称与最多 3 个唯一、共享且已确认的适用别名，保留原两个检索组和上限；不会读取初始事件答案来编排盲测查询。普通更新申请沿用去重、预算、取消与恢复；已明确人工绑定的等待申请可进入业务队列，已取消或终态申请不重启。

融资新材料保存为待核实观测：同事项补充来源、更正及字段差异在公司页可见，已确认初始资料继续保留。动态页面或验证码分别记为读取缺口，不自动增加调用或绕过限制。2026-09-13 已按批准部署并完成 E4.3 单公司对照；初始资料可读，自动正文证据输出未通过，详见有效看板。常驻开关仍关闭；本节不授权新一轮运行。关闭新路径可回退处理逻辑，带融资观测时不得降级删除 `0033` 策略。

### 政府官方工商身份导入

```bash
mkdir -p data/private/identity_imports
cp data/sample/official_identity_import.json data/private/identity_imports/official-example.json
export IDENTITY_IMPORT_FILE=official-example.json
export IMPORT_USER_ID='replace_with_local_admin_reviewer_uuid'
IDENTITY_IMPORT_DRY_RUN=true uv run python -m scripts.import_official_identity_json
uv run python -m scripts.import_official_identity_json
```

`dry-run` 必须显示零数据库写入、零外部调用、零 Token 和零费用。正式执行前核对证据域名为 HTTPS 政府/GSXT、信用代码及校验位、工商全称、注册地、登记状态和核验时间。`conflict` 不得通过手工 SQL 改为 verified；必须在专用工作台选择并留理由。重复文件应返回 `duplicate`。

V1 只接收不超过 1 MiB、最多 500 条且许可为 `public` 的 JSON。不得放入内部财务、投资协议、投委会材料、API Key、Cookie 或商业数据库受限内容；原始文件由操作者在私有目录管理，不进入 Git，也不会被系统复制到存储。当前没有网页/API 上传入口。

### 交易所披露资料人工身份核验（ADR-0019）

仅在 `0026` 合并部署后使用；本节不是打开研究开关的指令。复用上方 CLI，JSON 结构参考 `data/sample/exchange_identity_import.json`（完全虚构，不可当真实证据导入）。真实文件放在 `data/private/identity_imports/`，设置 `IDENTITY_IMPORT_FILE` 后先执行同一 `IDENTITY_IMPORT_DRY_RUN=true` 命令；应显示 `verification_basis=exchange_disclosure` 和零数据库写入/外部调用/Token。

人工核对港交所正式披露 PDF 的发行主体、工商全称、代码及校验位、注册地区、披露日期和页码。填 `identity_fields_confirmed=true`、包含信用代码的最小 `evidence_excerpt`、`evidence_locator`、`review_reason`；不得从其他主体或文件拼接必要字段。`data_updated_at ≤ checked_at ≤ queried_at`；未知登记状态保持未知，不把历史披露说成实时工商查询。当前只准入 ADR-0019 明列的 HKEX 域名和正式 PDF 路径；程序不联网下载或自动理解文件。

正式导入者须 active 且同时有机构管理员、平台管理员；重复文件返回 `duplicate`。冲突保留原公司数据，在原身份工作台处理时额外要求有效平台管理员及原审核员/机构管理员。核验文档和人工审计只在本租户私有层保存，不能随共享公司身份一起公开。许可 `public` 不代表可商业再分发全文；本流程不生成共享事件或研究任务。核验完成后仍须按原请求审批及 U01 单任务预算单独执行。

迁移 `0026` 只扩展两项依据约束，不回填既有数据。升级前后备份；没有新依据记录时可降级，任一相关表已有新依据记录时程序会拒绝 downgrade。不得改标来源或删记录强行回退，优先保留数据库结构并评估应用版本回退。

### 旧商业数据供应商 R1 退役部署

R1 合并前不得删除生产 Secret、缓存或历史数据库记录。正确顺序是：

1. 确认代码 PR 的迁移、RLS、后端、前端和 CI 全部通过；
2. 对生产 PostgreSQL 创建加密、可读且可恢复的升级前备份；
3. 部署新镜像并用 Alembic 从 `0021` 升级到 `0022`；
4. 核对只有旧商业来源支撑的身份已恢复为待核验、只有旧来源支撑的展示事实已撤回，同时独立政府核验、独立证据事实、基金关系和个人数据未受影响；
5. 完成真实账号搜索、详情、关注、基金叠加、审核工作台和权限负向回归；
6. 再创建升级后备份，最后删除主机 Secret、私有供应商缓存和部署环境中的旧专用变量。

`0022` 是保守的向前迁移：它保留历史来源、原始文档、身份核验、用量、任务和审计血缘，不把旧来源改称政府或公开网络来源。旧供应商证据引用统一停止展示；同时存在独立证据的事件仍可保留并只展示独立证据。降级只恢复旧 Schema/RLS 兼容性，不会自动重新核验身份、重新发布已撤回事实或重新开放证据引用；存在真实数据的环境不得把数据库 downgrade 当作首选回滚，优先回退应用版本并保留向前兼容的数据库结构。

生产清理只允许在新版本健康且备份可恢复后进行。清理时不得打印 Secret 内容，不得使用 shell 追踪模式；只删除已确认属于旧供应商的权限受限凭据文件、专用缓存目录和部署环境变量。旧 release 目录可按既有发布保留策略轮换，不能为了消除字符串而破坏仍承担回滚作用的最近版本。数据库中的必要历史审计不得物理删除。

个人研究申请、取消、临时额度和断线恢复状态继续由 PostgreSQL 保存。R3 合并部署前，申请仍只保存而不调用外部服务；不得用手工 SQL 把申请伪造成已完成，也不得恢复旧 Worker。R3 部署后，平台管理员应通过审核工作台把准确主体关联到既有的已核验共享公司，不能根据搜索摘要自动创建或核验公司。

本机曾由供应商 CLI 保存的用户级凭据，只在确认 R1 分支测试完成且不再需要回退旧工具后删除；不要读取、复制或记录其值。任何浏览器扩展、独立 CLI 或技能目录如仍存在，也必须按产品清单单独卸载或删除，但不得误删与本项目无关的通用工具。

### R3 受限公开网络研究 Worker（默认关闭）

R3 只在独立 `web-research-worker` 容器中联网。API 和前端不读取百度或博查密钥，页面搜索、详情、刷新和取消永远只访问 PostgreSQL。部署后先保持所有 R3 开关为 false，升级数据库到 `0023` 并完成 RLS 回归；再以平台管理员身份核对申请对应的是“信用代码和工商全称一致、已核验、允许进入共享目录”的公司。

零网络 dry-run 不要求 Provider 密钥，也不写库：

ADR-0020 主体查证 V2：新公司由后台独立核对名称/代码后才能进入研究，仍不接受搜索摘要作为证据。`--dry-run` 同时输出身份与业务研究预算，默认身份 6 次搜索/24 次 HTTP/4 MB，业务仍为 4 次搜索/8 次 HTTP/2 MB；平台日/月限额共同约束。运维提高身份预算时不清空已消耗计数，不直接修改请求为核验通过；在途旧版本可保留审计进度续做，已终止请求不能靠常驻重试恢复。后台只保存最小定位摘录，不保存完整网页正文。该条替代上文早期 R3 必须人工关联公司才能研究的限制，不改变正式官方核验与 `public_crosscheck` 的语义区别。

```bash
docker compose -f compose.production.yml -f deploy/compose.single-host.yml \
  --env-file deploy/single-host.env --profile web-research \
  run --rm web-research-worker \
  python -m scripts.run_web_research_worker --dry-run
```

真实运行前，把 `BAIDU_SEARCH_API_KEY` 和 `BOCHA_SEARCH_API_KEY` 只写入权限受限且 Git 忽略的生产环境文件，不得粘贴到命令、聊天、日志或 Git。更换 Key 时使用终端不回显输入并要求重复确认，原子替换前创建 `0600` 回退备份；完成后只记录不可逆短指纹，不打印或通过命令参数传递 Key。四个 Worker 专用开关必须同时临时设为 true：`WEB_RESEARCH_WORKER_ENABLED`、`WEB_RESEARCH_EXTERNAL_CALLS_ENABLED`、`WEB_RESEARCH_WORKER_CALLS_ENABLED`、`WEB_RESEARCH_PAID_API_CALLS_ENABLED`。最后一个开关表示“这是受额度或可能计费的调用，需要明确批准”，不表示本次一定收费。`AUTO_REFRESH_ENABLED`、`AUTO_PUBLISH_ENABLED`、可信来源调度和投资解读 Agent 必须继续为 false。

首次生产验收只处理一个已批准申请，并用 `--once` 单步观察 `company_research_jobs`、个人申请进度、`usage_ledger`、搜索缓存、原网页主体匹配和候选作用域：

```bash
docker compose -f compose.production.yml -f deploy/compose.single-host.yml \
  --env-file deploy/single-host.env --profile web-research \
  run --rm web-research-worker \
  python -m scripts.run_web_research_worker --once
```

确认百度有主体准确、非资料页且未明确过期的结果时，博查调用为 0；只有百度失败或缺少上述合格结果才允许回退。搜索摘要不能生成事件，必须存在经安全抓取和主体匹配的原网页证据。任务结束或停止验收后，立即把四个 Worker 专用开关恢复为 false，并确认没有常驻 Worker。合格共享缓存可保留 14 天；取消申请不删除已取得缓存，但不得把未完成、跨公司或身份已变化的结果继续复用。生产数据库降级前必须备份并评估真实缓存与 RLS 数据，优先应用版本回退并保留向前兼容 Schema。

R3.5 部署时把 `WEB_RESEARCH_POLICY_VERSION` 设置为 `bounded-web-v3`，`WEB_RESEARCH_RECENT_CHANGE_WINDOW_DAYS` 继续为 `365`。先创建可读、可恢复的加密备份，再升级到 `0025`；该迁移只扩展候选解读的 RLS，不创建新业务表。策略升级不使既有搜索缓存失效。先在生产备份的隔离副本中使用明确拒绝联网的 Provider、抓取器和模型完成卧安机器人缓存回放，确认搜索/抓取调用、模型 Token、新事件和自动发布均无新增；通过后才允许一次性真实运行。后续若暴露配置或质量问题，必须关闭调用开关，在代码与配置修复后复用同一家公司验证；不得连续查询新公司来代替根因修复。

百度诊断只查看 `company_research_jobs.coverage.search_groups.*.providers` 和对应 `usage_ledger.metrics` 中的检索组、错误类别、HTTP 状态及调用数。允许的类别包括 `authentication_failed`、`permission_denied`、`request_rejected`、`rate_limited`、`quota_unavailable`、`invalid_request`、`upstream_timeout`、`provider_unavailable` 和兜底 `http_error`。不得为排错输出 `web_search_cache_entries.query_text`、API Key、供应商错误消息或原始响应正文；个人用户只看任务状态，不看 Provider 内部诊断。


### 投资者重要变化解读 Agent V1（默认关闭）

数据库升级到 `0021` 后，可先用平台管理员身份执行零网络 dry-run。dry-run 只统计达到重要性门槛的已发布共享变化，不建队列、不读密钥、不调用模型：

```bash
export WORKER_TENANT_ID='replace_with_platform_operator_tenant_uuid'
export ANALYSIS_WORKER_USER_ID='replace_with_platform_admin_user_uuid'
APP_MODE=demo uv run python -m scripts.run_investor_analysis_worker --dry-run
```

真实运行只使用独立 Worker，不在 API 或公司页面同步调用。运行前必须将 DeepSeek Key 写入 Git 忽略且权限受限的部署 env，按供应商当期书面价格填写输入/输出 Token 单价；不得把 Key 放入命令、聊天、Git 或日志。只在受控窗口临时开启 `INVESTOR_ANALYSIS_ENABLED` 以及 Worker 专用的外部/可能计费开关；`AUTO_REFRESH_ENABLED`、`AUTO_PUBLISH_ENABLED` 和可信来源开关必须保持 false。旧商业数据调用开关已由 R1 删除。单机部署可用 analysis profile 排空当前队列：

```bash
docker compose -f compose.production.yml -f deploy/compose.single-host.yml \
  --env-file deploy/single-host.env --profile analysis \
  run --rm investor-analysis-worker
```

Worker 处理两类互不混淆的派生解读：`published + deterministic_change` 的已确认重要变化，以及 `candidate + unconfirmed_lead + bounded_public_web_page` 且由本次新增合格证据产生的待核实候选。后一类还必须具有同公司已核验实体提及和已完成研究任务；缓存复用、取消任务及私有原文不入队。无新变化、静态基线、低价值变化和重复输入均为零模型调用；两类任务共用月 Token 上限，超限标记 `budget_deferred`。输出必须通过对应 JSON Schema、事件/证据绑定、数字、信用代码和投资建议禁语校验；失败时不展示半成品，不改写事件，不发布事实，也不生成报告。运行后分别核对两种 `usage_ledger.operation`，再立即恢复全部专用开关为 false。

### 身份例外与历史审核工作台 V1（仅本机受控环境）

确认 API 只绑定 `127.0.0.1` 并设置 `REVIEW_WORKBENCH_ENABLED=true`；前端 `DEMO_USER_ID` 必须是当前租户内同时具有 `reviewer` 和 `institution_admin` 角色的本地用户，才能修改身份主数据。访问 `/reviews` 后，新导入通常只出现身份歧义；只能从 30 天内、与该提及相关且明确标注政府官方依据的候选中选择。历史商业身份核验只作审计，不再进入可选候选。提交后核对审核状态、公司信用代码/全称、实体提及、事件证据和发布路由；原 URL 未检查时应为 `unconfirmed_lead` 且不进入快照。工作台读取和身份决定都不调用外部 Provider。

完成私有验收后关闭前后端并取消该开关。当前 Header 身份可被伪造，禁止将工作台暴露到公网、局域网共享地址或多人环境；生产部署必须先实现正式认证和会话保护。

### 受控可信来源监测 V1（仅本机受控环境）

先以平台管理员打开 `/monitoring` 登记已核验公司、来源名称、类型、根域名、HTTPS 起始 URL、访问依据、许可、频率和保留策略。列表页应先用 dry-run，并在实样中确认内容链接路径；如导航链接过多，设置明确的列表内容路径前缀。dry-run 入队后以四个业务安全开关均关闭的 Worker 处理，核对请求、字节、Token 和费用全部为 0。

真实免费 HTTP 检查仅在一次性受控窗口中临时设置 `EXTERNAL_CALLS_ENABLED=true` 和 `TRUSTED_SOURCE_CALLS_ENABLED=true`，同时保持 `PAID_API_CALLS_ENABLED=false`、`AUTO_PUBLISH_ENABLED=false`。人工入队时 `AUTO_REFRESH_ENABLED` 可保持 false；只有到期调度入队时才在受控窗口临时开启。每次 Worker 只领取一个任务；运行后核对 `source_check_runs`、`usage_ledger`、robots、失败状态、候选公司归属和去重结果，再立即恢复外部与调度开关为 false。404、robots 拒绝、DNS/对端异常、MIME、超时或大小超限不得手工改成成功。

将候选标记为“值得研究”后，可在同一页面填写谨慎标题、最小证据摘录、事件分类、五项评价、结构化事实和不确定性。提交会复用既有人工研究导入服务，保留候选、来源、导入批次、原始文档、事件和证据血缘。重复提交返回同一份导入结果。`public_access` 只能建立私有研究；`permission_confirmed` 才可在另一次人工审核后晋升共享；`unclear` 和 `restricted` 必须先更新授权依据。交接不自动晋升或发布。

到期调度器为 Cron 可调用的一次性入口，默认 dry-run，不写库、不联网：

```bash
export WORKER_TENANT_ID='replace_with_local_tenant_uuid'
export SOURCE_MONITOR_WORKER_USER_ID='replace_with_local_platform_admin_uuid'
APP_MODE=demo SOURCE_MONITOR_SCHEDULER_DRY_RUN=true \
uv run python -m scripts.queue_due_source_checks
```

实际入队前必须同时显式设置 `AUTO_REFRESH_ENABLED=true`、`SOURCE_MONITOR_SCHEDULER_ENABLED=true`、`EXTERNAL_CALLS_ENABLED=true` 和 `TRUSTED_SOURCE_CALLS_ENABLED=true`，且两个付费/发布开关仍为 false。每次最多入队 `SOURCE_MONITOR_SCHEDULER_MAX_SOURCES`（默认 10）个来源；连续失败按检查间隔的 2、4、8 倍退避，上限由 `SOURCE_MONITOR_FAILURE_BACKOFF_MAX_MULTIPLIER` 配置。调度器只入队，不替代 Worker；既有请求级重试、活动任务合并和过期租约恢复继续生效。

## 4. 常见事件处置

| 事件 | 立即动作 | 恢复条件 |
| --- | --- | --- |
| 预算超限 | 停止新外部调用，任务标记 `budget_deferred`，保留查询 | 管理员调整预算或进入新周期；不得自动换贵源 |
| Provider 故障/限流 | 记录错误与 `retry_after`，有限重试；必要时使用已批准低成本替代 | 健康检查恢复，积压在预算内消化 |
| Worker 崩溃 | 不手工重复创建任务；等待租约过期后重领 | 检查点、幂等键和用量记录一致 |
| 严重负面误报 | 若误入已确认层，立即从快照撤下并标记撤回；保留证据、策略原因与审计 | 完成纠错或驳回，回归测试证明同类记录只进入未确认线索 |
| Secret 疑似泄漏 | 关闭 Provider、吊销并轮换、检索日志和提交历史 | 新 Secret 通过最小权限验证，完成事件复盘 |
| 跨基金越权 | 立即禁用相关账户/服务身份，保全审计，关闭受影响入口 | 修复 RLS/授权并通过负向回归，通知义务已评估 |
| 许可到期/撤稿 | 停止抓取和展示受限正文，标记来源与相关事件 | 获得新授权或完成撤回/替代证据审核 |

## 5. 备份与恢复

每日加密 PostgreSQL 备份，文件存储按许可和变化量备份，备份与主机隔离并设保留期。每月至少演练一次恢复到隔离环境：恢复数据库与文件 → 校验迁移版本 → 校验哈希和证据外键 → 重置过期租约 → 验证 RLS → 用虚构用户冒烟测试。Demo 规划目标为 RPO 24 小时、RTO 8 小时；正式 SLA 需另定。

## 6. 删除与退出

删除先冻结访问并生成范围清单，区分用户账户、租户私密数据、共享公开事实和依法/依约需保留的审计。异步清除后验证数据库、存储、缓存和备份生命周期；出具不含私密内容的审计结果。停止服务时先关闭外部调用和 Cron，再排空/终止任务、备份、撤销 Secret，最后关闭 API。

## 7. 变更控制

更新频率、事件分类、评分、权限、Provider、成本上限、重大负面规则、财务口径或 Kimi 边界发生变化时，先更新 ADR、迁移/配置和测试，再部署。历史迁移不回改。实施命令、结果和阻塞只写入简洁的[实施记录](IMPLEMENTATION_LOG.md)。
