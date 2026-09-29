# Investment Backend 文档说明

当前架构入口为仓库根 `README.md` 与 `k8s/sunmoonai/docs/project-guide/`；
部署事实须核对实际 bundle 与运行状态。历史迁移/退役证据按
`k8s/sunmoonai/docs/legacy-backlog/verification-index.md` 的固定 Git 版本查询。

本目录中的 `mooc-manus-v4-*`、部分早期 ADR 及其中出现的 `research-app`、
`research-admin-backend`、`research-web-frontend` 等名称，是 Investment 改名前的历史设计与
交付记录。它们用于审计 Git 沿革，不代表当前仓库、运行资源或部署入口；其中命令不得直接执行。

当前唯一活动领域名为 `investment-app`，唯一 Backend 为 `investment-backend`。未来如果创建
新的通用 `research-app`，必须从最新模板独立实例化，不复用这里的历史身份、数据库或运行资源。

## 2026-09-29：旧运行时已删除

2026-07 的 LangGraph 智能体运行时（`application/agent`、`domain/agent`、`infrastructure/agent`、
`infrastructure/graph`，以及它的路由、后台任务、检索客户端、探针脚本和测试）按所有者的决定删除。
规则见 k8s 仓 `tree-build/SDD/architecture/engineering.md`「工程结构」。

| 还留着的 | 原因 |
| --- | --- |
| 数据库里它的表 | 已发布的迁移不删不改；以后用新的迁移去掉 |
| `alembic/versions/` 里它的迁移文件 | 同上 |
| 本目录的 `mooc-manus-v4-*` 与 `adr/` | 历史设计记录，是否删除等所有者定 |
| `scripts/pair_fixture.py`、`scripts/delivery_runtime_probe.py` | 模板带来的，要在模板里处理 |

现在的产品代码是工作台：`app/application/workbench/`、`app/domain/workbench/`、`app/infrastructure/workbench/`。

