# 本地代理接入配置与契约

代理令牌新签发有效期 30 天；已有令牌不被自动替换，其它令牌仍按原期限。
页面显示存量令牌的实际到期时间，旧不透明令牌无到期值时明确显示未知。

## 下载

唯一配置 `WORKBENCH_AGENT_DOWNLOAD` 是 JSON；未配置时
`GET /api/workbench/agent/download` 返回 `download: null`。接口须 Web 身份，响应 no-store。
托管由运维确定；配置支持外部 HTTPS 地址与后端转发两种，网页始终接收相同六个公开字段。
对象名、存储端点和凭据不返回网页，也不接受客户端指定。

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

### 两种托管方式

- `mode: external`（省略时默认）：上述公开六字段；不允许 `object_key`。将来切边缘只改这里的 mode/url，代理和网页无需改代码。
- `mode: object-storage`：加上 `object_key: windows-x64/<版本>/<文件>.zip`，
  `url` 必须等于 `WEB_FRONTEND_BASE_URL + /api/workbench/agent/package`。
  桶固定 `agent-releases`。必须另配私有 JSON 环境输入 `WORKBENCH_AGENT_RELEASE_STORAGE`：
  `endpoint`（HTTPS）、`region`、`ca_file`、`access_key`、`secret_key`。
  只给 API 角色该桶的 GetObject 身份，禁止上传和管理权限；不从默认 AWS 凭据链或代理环境取值。
  Kubernetes 配置真源及操作说明在 k8s 的
  `gitops/components/app-platform/investment-app/investment-backend/agent-releases/`。

### 转发接口

`GET` / `HEAD /api/workbench/agent/package` 须 Web 登录；无配置或外部模式返回 404；
查询参数一律拒绝，不能传对象名、地址、摘要或凭据。使用存储 HEAD 核对长度和元数据，
再用对象 ETag 条件读取；拒绝存储跳转，TLS 验证使用专用 CA。预读失败返回 503，不泄露上游响应。

- 全量 200，单段 `Range: bytes=start-end`、`start-`、`-suffix` 返回 206。
  无效、多段或不可满足范围返回 416 和 `Content-Range: bytes */<总大小>`。
- 返回 `Content-Length`、`Accept-Ranges: bytes`、ETag（ZIP SHA256）、
  `X-Checksum-Sha256`、`X-Manifest-Sha256`；两个摘要均表示**完整包**，不是当前片段。
- 客户端续传应携带 `If-Range: "<ZIP SHA256>"`；不一致返回完整 200，禁止拼接不同发行包。
- 每次最多读取 64 KiB，保留末块直至长度/全量摘要核验结束；不整包读入内存。
  断开或异常会关闭上游流；已发送响应头后的故障只能中断下载，不能改成 JSON 错误。
  Range 依赖已核验的不可变对象，最终安装前仍须校验完整 ZIP 与清单。
- 所有成功与失败响应 no-store；下载不会签发令牌或改变账号。

单段范围与条件写语义参考 [S3 GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)
与 [条件写入](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)。AIStor 实机验收独立于 SDK/接口本地测试。

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
发布后还须实际验证浏览器下载、安装、领令牌和在线。测试库只能使用专用 `_tests` 数据库。
