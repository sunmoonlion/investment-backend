# 本地代理接入配置与契约

代理令牌新签发有效期 30 天；已有令牌不被自动替换，其它令牌仍按原期限。
页面显示存量令牌的实际到期时间，旧不透明令牌无到期值时明确显示未知。

## 下载

唯一配置 `WORKBENCH_AGENT_DOWNLOAD` 是 JSON；未配置时
`GET /api/workbench/agent/download` 返回 `download: null`。接口须 Web 身份，响应 no-store。
托管由运维确定，后端不下载/代理任意地址，不预置候选下载链接。

```json
{
  "url": "https://downloads.example/agent/RELEASE/windows-x64.zip",
  "version": "0.2.0",
  "zip_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "codex_version": "0.155.1",
  "size_bytes": 174242372
}
```

上面仅结构示例，不可作为真实发布记录。字段齐全，HTTPS 地址不能含认证、查询串或片段；
未知字段、坏摘要、非正大小启动配置校验失败。发布前用真实包计算摘要、大小，并核对 Codex 兼容版本。

## 领取和轮换

- `POST /api/workbench/sandboxes/provision`：仍需先登记模型 key。首次返回 `relay.agent_token`，
  重复调用不再次给令牌；`relay.agent_token_expires_at` 为 UTC ISO 时间或 null，
  `relay.identity_revision` 是非敏感版本标记。
- `GET /api/workbench/sandboxes/provisioned`：顶层返回 `agent_token_expires_at`、
  `identity_revision`，不返回令牌。未创建身份时无这些字段。
- `POST /api/workbench/sandboxes/relay-identity/rotate`：必须提交
  `{"expected_revision":"从刚才状态取得的64位摘要"}`。旧版本返回
  `409 relay_identity_changed`，不撤销新令牌；刷新后须用户重新明确操作，不能自动重试。
  成功只在本次响应给新令牌，旧令牌吊销；在线沙箱可能滚动。
- 同一用户签发、轮换、删除同时发生时，`409 relay_identity_busy`，先查状态。
  PostgreSQL 独立连接上的事务咨询锁覆盖整个操作，业务提交不会提前释放；异常或取消由事务结束释放。
  身份记录先提交，再做外部注册；外部失败不假装跨系统原子成功，响应丢失须查状态/明确重领。
- 所有 `/api/workbench/` 响应（含错误）no-store。沿用 Web 身份、资源归属与 Origin/CSRF；
  不接收客户端指定 owner、令牌或到期时间。

后端与网页须配套发布：旧网页轮换未带 expected_revision 将被拒。不能仅发布后端就宣称接入闭环完成。
本轮尚未发布，下载配置保持未设置。测试库只能使用专用 `_tests` 数据库。
