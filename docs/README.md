# Investment Backend 文档说明

当前架构、部署与发布事实以仓库根 `README.md`、K8s Architecture v2 bundle 和
`k8s/sunmoonai/docs/architecture-v2/` 证据为准。

本目录中的 `mooc-manus-v4-*`、部分早期 ADR 及其中出现的 `research-app`、
`research-admin-backend`、`research-web-frontend` 等名称，是 Investment 改名前的历史设计与
交付记录。它们用于审计 Git 沿革，不代表当前仓库、运行资源或部署入口；其中命令不得直接执行。

当前唯一活动领域名为 `investment-app`，唯一 Backend 为 `investment-backend`。未来如果创建
新的通用 `research-app`，必须从最新模板独立实例化，不复用这里的历史身份、数据库或运行资源。
