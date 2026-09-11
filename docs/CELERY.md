# Investment Backend 异步任务与运行角色

`investment-backend` 的 API、Worker、Scheduler、Migration 来自同一源码和不可变镜像，分别通过 `app.bootstrap.api`、`app.bootstrap.worker`、`app.bootstrap.scheduler`、`app.bootstrap.migration` 启动。

Worker 不是独立源码项目。API 将 Agent run/恢复输入与 Outbox 同事务提交，不直接发布图任务。Scheduler 每 5 秒触发公共投递策略；Agent 适配器把执行命令送 Celery、可重建通知送 Redis。通用 topic 与 Agent topic 使用不相交的策略范围，发布/重试/死信循环只有模板那一份。各角色必须使用不同 ServiceAccount、Secret、资源与伸缩策略。

Agent 保留 PostgreSQL 会话级执行租约、epoch、取消与副作用回执。每个 Agent Celery 任务结束（成功或失败）都关闭本任务的 PG/Redis 池，避免与通用任务交替运行时跨事件循环复用连接。旧直接 graph 入口拒绝执行，旧 producer 方法已删除。迁移/恢复边界见 [公共可靠投递](durable-delivery-luna.md)。

新 K8s 资源由 `tpl-app/k8s-deployment` 的统一运行角色模型生成。旧 Research/Investment Worker 只属于 v1 回滚拓扑，在 R5/R7 门禁前保留，但不得作为新源码或新部署生成器。
