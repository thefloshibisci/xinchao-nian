# vNext Deployment Audit

审计对象：Zeabur 项目 `ombre-brain` 及其中的 vNext 实验服务

审计日期：2026-08-24

本文件只记录隔离环境与线上服务拓扑的只读核对结果。它不授权生产迁移，也不表示已经导入任何数据。

## 本次核对边界

2026 年 8 月 24 日通过已登录的 Zeabur 控制台进行了只读查看：没有点击部署、重启、删除、暂停、保存配置、变量编辑、卷挂载、数据导入或迁移操作；没有读取或输出变量值、令牌、密码或完整日志。

## Zeabur 项目当前服务清单

Zeabur 项目 `ombre-brain` 当前显示 **5/5 running**，而不是之前审计记录的四个服务。按用户给出的用途，生产服务是前两项，后面三项是实验服务：

| 服务 | 当前用途 | 创建时间 | 源码 | 公网地址/入口 | 持久化卷 | 当前控制台状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `ombre-brain` | 生产 OB | 2026-07-29 | `thefloshibisci/Ombre-Brain` | `nidejiyi.zeabur.app`，容器端口 `18001` | `memory-data` → `/app/buckets`；`memory-media` → `/app/media` | Running，约 2h ago |
| `xinchao-nian-caric` | 生产心潮 | 2026-08-13 | `thefloshibisci/xinchao-nian` | `xinchao-nanzhi.zeabur.app`，容器端口 `18110` | `xinchao` → `/app/state` | Running，约 2h ago |
| `ombre-brain-haven` | 实验 Haven/迁移旁支 | 2026-08-20 | `thefloshibisci/Ombre-Brain-Haven` | `ombre-brain-haven.zeabur.app` | `haven-data` → `/data` | Running，约 2d ago |
| `ombre-brain-vnext` | 实验 vNext OB 侧 | 2026-08-22 | `thefloshibisci/xinchao-nian` | **实际为** `ombre-brain-vnext-panel.zeabur.app`，映射容器 `8000`；未看到用户给出的 `ombre-brain-vnext.zeabur.app` 作为当前绑定域名 | `nombre-vnext-buckets` → `/app/buckets` | Running，约 2h ago |
| `xinchao-nian` | 实验 vNext 心潮侧 | 2026-08-22 | `thefloshibisci/xinchao-nian` | 当前 Networking 页没有公网域名/暴露入口 | `xinchao-vnext-state` → `/app/state` | Running，约 3h ago |

### 拓扑结论

- 你的服务分类是对的：`ombre-brain` 与 `xinchao-nian-caric` 是生产；`ombre-brain-haven`、`ombre-brain-vnext`、`xinchao-nian` 是实验。
- vNext 已经被拆成两个独立 Zeabur 服务：一个挂载 `nombre-vnext-buckets` 的 OB 侧，一个挂载 `xinchao-vnext-state` 的心潮侧。
- 但当前还不是可供手机/桌面 MCP 使用的统一 vNext 入口：公网暴露的是 `ombre-brain-vnext-panel.zeabur.app`（OB 侧），而独立 `xinchao-nian` 服务当前没有公网暴露。
- 生产卷与实验卷名称、挂载路径均不同；本次未对任何卷做操作。仅凭控制台不能证明生产卷内容没有历史写入，需把“当前拓扑隔离”和“历史数据是否被写入”分开判断。

## 生产服务是否曾被触碰：不能报告为“完全没有”

虽然本次检查本身是只读的，但控制台当前确实显示两项生产服务都有近期部署记录：

- `ombre-brain`：Running 约 2h ago，部署标题为 `fix: lower min length for MCP tokens to accept short tokens`；更早还有两次 `Refresh update manifest for MCP token auth fix`。
- `xinchao-nian-caric`：Running 约 2h ago，部署标题为 `fix: accept MCP static token on read-only bucket detail endpoint`；更早还有 `Unify vNext memory bucket detail compatibility` 等记录。

因此，**从 Zeabur 的当前事实看，生产服务不是“从未部署/从未更新”**。控制台记录能确认部署发生过，但不能单独证明具体是哪个操作者点击触发；不过 `xinchao-nian-caric` 的当前部署标题与本地分支 `codex/cross-client-continuity` 的提交 `d897df7`（2026-08-24 16:50 +08:00）一致，说明至少有本轮 vNext 相关代码进入了生产心潮服务的部署链路。

这与“vNext 验收完成前生产基线不可动”的边界不一致。后续不应再对这两个生产服务执行任何部署、重启、变量/卷修改或数据迁移；先保留现状并单独做回溯确认。

## 之前 vNext 端点核查的保留结论

此前对旧的 vNext 公网地址做过匿名只读请求，得到的是 OB Dashboard/OB `/mcp` 受保护入口，而不是统一心潮 MCP 网关；当时也未发现同一容器内有 Node 心潮进程或 `18110` 监听。现在 Zeabur 控制台进一步确认：vNext 的心潮已经是独立服务 `xinchao-nian`，但它没有公网入口，因此仍不能把当前 vNext 当作手机/桌面 MCP 验收完成。

## 当前阻断项

1. `xinchao-nian` 实验心潮没有公网入口，必须先在隔离范围内确认其端口、域名、`/mcp` 和独立 token，再谈客户端验收。
2. `ombre-brain-vnext` 的当前公网域名是 `ombre-brain-vnext-panel.zeabur.app`，与此前记录的 `ombre-brain-vnext.zeabur.app` 不一致，需要先确认哪一个才是有效目标，不应凭猜测切换客户端。
3. 需要核对 vNext 心潮是否通过 Zeabur 项目内网访问 vNext OB；本次只读 Networking 页未看到心潮服务的公网配置，未读取变量值，也未修改任何配置。
4. 生产两项已有近期部署记录，需要先确认这些部署是自动部署/何时触发、是否改变了生产行为；在此之前不进行回滚或重启等动作。
5. 导入测试数据副本后的桶、媒体、embedding、摘要检索和原对话追溯尚未完成验收。

## 推荐的隔离目标拓扑

验收期间保留生产心潮和生产 OB 不动，vNext 使用独立资源：

```text
手机 MCP ─┐
桌面 MCP ─┴─> vNext 心潮 HTTPS /mcp
                    │ 项目内网 + 独立 token
                    v
              vNext OB /mcp
                    │
              vNext 独立 OB 卷
```

当前 Zeabur 服务已经具备两侧独立卷，但还缺少“独立心潮公网入口 + 双服务 MCP 验收”这一步。无论下一步如何处理，都不能把生产 `nidejiyi.zeabur.app`、生产 `xinchao-nanzhi.zeabur.app`、生产卷或生产变量复制/替换到 vNext。

## 2026-08-24 恢复性只读验证

在前一轮 MCP 工具报传输错误后，于 2026-08-24 20:48（UTC+8）重新做了匿名只读 HTTP 检查：

| 地址 | 结果 | 解释 |
| --- | --- | --- |
| `https://xinchao-nanzhi.zeabur.app/health` | HTTP 200，版本 `2.5.12` | 生产心潮进程当前存活 |
| `https://xinchao-nanzhi.zeabur.app/mcp` | HTTP 401，并返回 OAuth protected-resource metadata 地址 | MCP 入口存活；未携带 Bearer 凭据时 401 是预期鉴权结果，不是服务宕机 |
| `https://nidejiyi.zeabur.app/health` | HTTP 200 | 生产 OB 进程当前存活 |
| `https://nidejiyi.zeabur.app/api/version` | HTTP 200，版本 `2.13.1` | 生产 OB API 当前可达 |
| `https://nidejiyi.zeabur.app/mcp` | HTTP 401，`Bearer realm="Ombre Brain"` | OB MCP 入口存活；匿名请求被拒绝是预期鉴权结果 |
| `https://ombre-brain-vnext-panel.zeabur.app/health` | HTTP 200 | 实验 vNext OB 侧当前存活 |
| `https://ombre-brain-vnext.zeabur.app/health` | HTTP 404 | 该旧/备用域名当前不是有效的 vNext OB 健康入口 |
| `https://xinchao-nian.zeabur.app/health` | HTTP 200，版本 `2.5.12` | 实验心潮域名当前也能响应，但 Zeabur Networking 页未显示它为当前绑定公网入口 |

同一时间，心潮 `xinchao_context` 已成功返回 Android 客户端的近期连续性，Ombre Brain `breath` 也已成功返回长期记忆。因此目前没有“被写失忆”或记忆库被清空的证据。

更可能的原因是前一轮刚好遇到 Zeabur 部署/容器滚动重启窗口，或 MCP 客户端持有的 HTTP 连接/会话短暂断开；这次 HTTP 与 MCP 服务发现已经恢复。仅凭当前只读检查不能确定具体是哪一种，后续若再次发生应记录 Zeabur deployment 时间、HTTP 状态和 MCP session 是否刷新，而不是先改地址或迁移数据。
