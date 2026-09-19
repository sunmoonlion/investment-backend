# Investment Backend 文档说明

当前架构入口为仓库根 `README.md` 与 `k8s/sunmoonai/docs/project-guide/`；
部署事实须核对实际 bundle 与运行状态。历史迁移/退役证据按
`k8s/sunmoonai/docs/legacy-backlog/verification-index.md` 的固定 Git 版本查询。

本目录中的 `mooc-manus-v4-*`、部分早期 ADR 及其中出现的 `research-app`、
`research-admin-backend`、`research-web-frontend` 等名称，是 Investment 改名前的历史设计与
交付记录。它们用于审计 Git 沿革，不代表当前仓库、运行资源或部署入口；其中命令不得直接执行。

当前唯一活动领域名为 `investment-app`，唯一 Backend 为 `investment-backend`。未来如果创建
新的通用 `research-app`，必须从最新模板独立实例化，不复用这里的历史身份、数据库或运行资源。
