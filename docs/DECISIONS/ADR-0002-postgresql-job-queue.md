# ADR-0002：PostgreSQL 任务表加 Cron

- 状态：已接受
- 日期：2026-07-13

## 背景

Demo 任务量和并发低，已有 PostgreSQL 是主数据库。引入 Redis/Celery 会增加部署、备份、监控和故障面，但当前没有吞吐证据。

## 决定

用 `refresh_jobs`/`refresh_runs` 持久化任务与检查点，Cron 触发 Scheduler，独立 Worker 通过 `FOR UPDATE SKIP LOCKED` 和租约领取任务。任务有幂等键、心跳、过期重领、有限重试和用量记录；`JobQueueProvider` 隔离未来实现。

## 影响与升级条件

事务、权限和备份可复用，崩溃后状态可查；代价是队列流量会占用数据库。只有持续积压超过产品时效、锁/IO 明显影响查询，或需要高吞吐延迟队列和复杂工作流时，才评估 Redis/Celery，并先形成新 ADR。详见[系统架构](../02-system-architecture.md)。
