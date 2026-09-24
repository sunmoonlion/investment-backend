"""工作台的持久化：SQL 文本 + 事务边界。账在 PostgreSQL，事件只追加，状态用 state_version 比较交换。"""
