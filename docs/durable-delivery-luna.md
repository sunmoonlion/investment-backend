# Investment 公共可靠投递开发候选

本轮承接模板 → Info → Knowledge 已验证的公共实现。没有部署生产、迁移业务库，
也没有推送正式发布标签；实际验证结果及未覆盖边界以父仓对齐报告为准。

## 一个策略实现，两个领域传输适配

`AgentDelivery` 继承模板 `DurableDelivery`，不再复制领取、发布完成、退避、死信或
重放逻辑。topic 映射 `agent.execution → agent.executor`，`agent.notification → None`；
通知是可从 DB 事实重建的提示，不要求业务 Inbox。`active_execution_sql` 使用已有
Agent 会话级执行租约，公共对账不能把活跃执行当成丢失消费。

Agent 的 pump 只提供 Celery 命令引用和 Redis 通知适配，循环委托模板 pump。
Worker 的通用 pump 处理显式 handler 注册的其它 topic（当前默认空）；两者范围不交叉。
旧 `dispatch_agent_graph` / `dispatch_pilot_graph` 已删；旧任务入口保留拒绝执行，
不能重新开启旧无 fencing 路径。`LeaseLost` 是公共投递异常的兼容导出，不另造处理策略。

AgentTransactions 的原子 run/event/Outbox、单调 epoch、heartbeat、取消、Graph Port、
checkpoint 空间与副作用账均保留。只有过期执行的工具副作用状态对账仍属于 Agent 扩展。
副作用 unknown 不因重放而重做，具体远端 Provider 必须另有真实幂等/回执/fencing 证据。

真实 Celery 验证曾发现两个入口交替运行时复用跨事件循环的 PG 池。当前 Agent 任务
在成功或失败退出时均关闭自己的 PG/Redis 池；不改动 API 池生命周期，也不靠关闭
公共调度隐藏问题。支持的 Celery 运行形态沿用进程隔离 Worker（测试使用 solo），
没有验证线程池并发调用同一 event loop。

## 迁移与回滚

`20260911_0007` 接既有 `20260910_0006`。切换前停止旧写入和 Worker、备份并实际恢复，
再由独立 Migration Job 执行；API/Worker/Scheduler 均不隐式迁移。

1. 公共表承接全部旧 `agent_delivery_failures`，保留失败时间与 replayed_at。
2. 旧表改名 `agent_delivery_failures_legacy_0006`，触发器拒绝增改删和 TRUNCATE；
   旧表名不存在，旧 Worker 会失败而不是继续写第二份死信。
3. downgrade 先拒绝活跃执行、未消费的非 Agent 命令和非 Agent 死信；不得清空表绕过。
4. 允许 downgrade 时，将本轮新增/修改/重放的 Agent 死信带回旧表，再撤销公共增量。
   run、事实、Inbox、工具副作用、接受的 execution_state 保持不动。

回滚前须盘点在途命令和 unknown 远端副作用；存在新类型任务时走整库备份恢复并
单独处理窗口内新请求，不允许旧 Worker 猜测执行新任务。旧档案须保留至观察窗结束。

## 运维

```bash
python scripts/agent_delivery_admin.py preflight
python scripts/agent_delivery_admin.py dead-letters
python scripts/agent_delivery_admin.py unknown-effects
python scripts/agent_delivery_admin.py reconcile
python scripts/agent_delivery_admin.py replay --message-id <uuid>
```

Agent CLI 的死信查询、执行领取及重放全部使用 `outbox_dead_letter`，重放保留 Inbox。
通用 `app.cli.durable_delivery` 仅操作默认 handler 注册范围，不能用空范围代替 Agent CLI。

原始本地完整 173 passed；最终完整 198 passed，无跳过。真实容器发现资源生命周期
与导入顺序缺口后新增 6 项入口测试并修复，最终公共和 Agent 两段容器探针均通过。
19 张表实际备份恢复一致。详细容器、身份、网络和未覆盖边界见父仓
`docs/template-alignment-durable-delivery.md` 与工作区接手证据，不把中间通过当最终验收。
