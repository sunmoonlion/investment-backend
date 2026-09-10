# Agent 可靠性修复记录

作者：Luna。实施日期：2026-09-10 至 2026-09-11。
基线：investment-backend `18d88c7`。原始请求：「那你先修吧，修好把这段去掉」。
范围：分析文档原 8.6 的事务投递、执行租约与副作用、会话级执行接口三项；不替换 UI，不接入新的模型 SDK，不在业务环境执行迁移或部署。

## 改动与依据

| 原问题 | 本次实现 | 适用约束 |
| --- | --- | --- |
| domain / UI 分次 commit，run 与投递存在断点 | AgentTransactions 管理外层事务；domain、UI、通知 Outbox 一起提交；Phase-0 和 Pilot 创建、恢复与命令 Outbox 一起提交 | D1/D2、C1/C2 |
| 消息只有发布动作，没有完整恢复链 | AgentDelivery 复用既有 Outbox/Inbox；消费回执随结果提交；投递租约、退避、死信、人工重放、周期性对账；缺消费回执的已发布命令重新投递 | C1/C2、A3 |
| Redis GET 与续租/释放分开执行 | Lua 原子比对并操作；PostgreSQL 执行租约才是写入依据，含单调 epoch、持续续租及过期拒写 | A2/A3 |
| 旧 worker 可以迟到写状态、事件或结果 | worker 的事务在持租约行锁时验证 owner、epoch、command 和有效期；取消提升 epoch；持久结果、事件和 inbox 同事务 | 产品 I14、F-DISPATCH-02 |
| 一行 completed 被当作外部动作保证 | DB-local effect 明确仅代表本库事务；远程动作使用 DurableSideEffectService：意图、executing、unknown、稳定业务键、回执及查询对账；未知结果不盲重试 | A3、既有 Side Effect 合同 |
| 单次调用 Port 表达不了执行会话 | 新增中性 AgentExecutorPort；当前两种 graph 均由 GraphExecutor 接入，业务执行与 SDK 细节分离；Fake executor 验证取消和迟到结果 | A4/A5 |
| checkpoint 恢复可能读到旧执行者写入 | 接受的 ExecutionBinding 落 agent_runs；每次执行使用独立 checkpoint thread；两种现有纯节点图从已接受状态恢复 | D1、产品 F-EXEC-05 |

不修改通用 Outbox 原语、通用表约束或模板公共接口。新增投递策略、死信和执行租约属于 Agent 扩展；worker 仅新增 Agent 任务注册及每 5 秒一次的投递/对账调度，因此不触发模板公共能力同步。迁移 `20260910_0006` 接在既有单链末端，并更新迁移文件清单测试（D6/D7）。接口分面继续复用应用服务，现有浏览器 DTO 和 Knowledge provider 合同不变（I1、C3/C4）。

## 可复跑验证

使用明确指定的独立测试库（库名以 `_tests` 结尾），每个测试建立并删除自己的随机 schema。不会修改业务数据库。

```bash
cd investment-backend/app
.venv/bin/ruff check .
.venv/bin/pyright
AGENT_TEST_DATABASE_URL=postgresql://<测试连接>/<独立库名_tests> \
AGENT_TEST_REDIS_URL=redis://<测试Redis>/0 \
.venv/bin/pytest -q
```

实际结果：完整套件 **170 passed, 2 skipped**；随后增加跨会话副作用校验，针对性 PostgreSQL 故障套件 **16 passed**；Ruff 与 Pyright 通过。两项跳过为原有测试的环境限制，基线同样跳过。数据库测试未设置连接变量时会明确跳过，不能用这种运行代替本次数据库验收。

验证覆盖：

- domain 写完、UI 写入失败时，状态、事件和通知全部回滚；
- 重复建单只有一个 run 和一个命令；恢复消费与 Outbox 失败同时回滚；
- 恢复请求响应丢失可重试，改变输入的同键重试被拒绝；
- 重复 worker 投递只产生一份完成结果与本地副作用；
- 发布后未记成功、消息未被消费时均可恢复，超过重试上限进入死信；
- 模拟进程失联并使租约过期后重领，旧 worker 不能改状态、事件、回执或释放新租约；
- 长执行期间续租，取消后迟到结果被拒绝；
- 远端动作成功、本地回执丢失时通过查询补记，未知结果不重新执行；
- 真 PostgreSQL checkpoint 跨执行恢复、真 Redis Lua 令牌检查；
- 新增迁移降级再升级后既有 run 保留。

故障测试通过异常注入、并发执行和测试库租约过期来制造故障窗口；不是生产 SIGKILL 演练，也不代表已验证真实外部业务服务的 fencing 支持。当前生产图节点无远程写副作用；未来接入有副作用的 provider，必须在其网关/目标端实施 RemoteEffectPort 的 fencing 和幂等合同，并补真实集成测试，不能仅靠传一个 epoch 参数宣称外部系统已支持。

## 发布与运维

本次交付源码与验证，未运行生产迁移、发布镜像或部署。

1. 停止新请求进入旧执行链，等待旧任务完成或经所有者处置旧的 waiting 任务；旧 worker 必须停止。新旧执行者不能混跑，因为旧 worker 不执行 fencing。
2. 在旧版本 schema 上运行 `python scripts/agent_delivery_admin.py preflight`；存在缺少新命令记录的旧在途 run 时拒绝切换，不猜测丢失的输入或批准。
3. 按既有数据库发布流程备份，由独立 Migration Job 升到 `20260910_0006`；API/Worker/Scheduler 不自动迁移。
4. 发布同一版本的 API、Worker 与 Scheduler。启动 Scheduler（Celery Beat）才能周期投递；Redis 只影响即时提示，事实可从 DB 回放。
5. 执行测试环境中的建单、等待、恢复、断连重放验收；观察死信和无回执命令。旧的直接 `agent_graph.run` / `pilot_agent_graph.run` 入口明确拒绝执行，避免绕过持久命令。
6. 回滚须先停新请求和新 worker，盘点新版本未完成命令与 unknown 副作用，保持数据库备份；不得让旧 worker 接管新版本尚未结算的执行。

运维命令沿用服务环境的数据库连接：

```bash
python scripts/agent_delivery_admin.py dead-letters
python scripts/agent_delivery_admin.py unknown-effects
python scripts/agent_delivery_admin.py reconcile
python scripts/agent_delivery_admin.py replay --message-id <死信UUID>
```

重放只重置投递尝试，不删除 inbox，也不重新执行已确认完成的命令。副作用 unknown 须通过具体 provider 的查询回执处理，不用直接改 completed 或重复调用掩盖未知结果。
