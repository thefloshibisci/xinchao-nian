# vNext Deployment Audit

审计对象：`ombre-brain-vnext.zeabur.app`

审计日期：2026-08-22

本文件只记录隔离环境的结构核对结果。它不授权生产迁移，也不表示已经导入任何数据。

## 线上已确认事实

以下检查均为只读公开请求，不携带令牌，不调用 MCP 工具，不写入记忆：

| 检查 | 结果 | 结论 |
| --- | --- | --- |
| `/` | HTTP 200，响应为 Ombre Brain Dashboard，响应头 `Server: uvicorn` | 当前公网根入口是 OB |
| `/health` | HTTP 200，`{"status":"ok"}` | 当前 OB 进程存活 |
| `/api/version` | HTTP 200，版本 `2.6.6` | 当前公网进程是 OB 2.6.6 |
| `/api/system/diagnostics` | HTTP 401 | 诊断接口受保护，不能从公网匿名确认卷和数据目录 |
| `/mcp` | HTTP 401，返回 OB OAuth resource metadata | 当前公网 `/mcp` 是 OB 的受保护 MCP，不是心潮网关 |

因此，目前不能从公网确认以下事项：

- Zeabur 服务内是否还运行心潮进程；
- 是否存在第二个内部 OB/心潮服务或反向代理；
- `ombre-vnext-buckets` 是否实际挂载到 OB 进程的 `/app/buckets`；
- 卷内是否存在 `config.yaml`、`embeddings.db`、桶 Markdown、`.embedding_outbox.json` 或媒体文件；
- 心潮是否能通过内部地址访问 OB，或者 `tools/list` 是否包含心潮工具与 OB 工具的统一集合。

## Zeabur 控制台与容器已确认事实

通过已登录的 Zeabur 控制台和 `ombre-brain-vnext` 服务终端进行只读检查，没有执行部署、重启、配置修改、卷操作或数据导入：

| 项目 | 已确认结果 |
| --- | --- |
| 服务源码 | `thefloshibisci/xinchao-nian` |
| 分支与部署 | `codex/cross-client-continuity`，当前部署标题为 `Update Ombre compatibility note` |
| Dockerfile | 服务设置显式指定 `/ombre-brain`，不是仓库根目录 `Dockerfile` |
| 启动命令 | 未覆盖镜像默认启动方式；PID 1 为 `python src/server.py` |
| 容器运行时 | 有 Python，没有 Node；因此该容器不可能同时运行心潮 Node 服务 |
| 监听端口 | `/proc/net/tcp` 显示 `0.0.0.0:8000` 监听；未发现 `18110` |
| 实际进程 | 除 PID 1 的 OB Python 进程外，只看到终端诊断产生的 shell 进程 |
| 实验卷 | `ombre-vnext-buckets` 挂载到 `/app/buckets`，容器内可见为独立卷 |
| 卷内容概况 | 存在桶目录、`config.yaml`、`embeddings.db`、`embeddings.db.backup`、`dehydration_cache.db`、`.embedding_outbox.json` 和日志文件 |
| 媒体顶层目录 | `/app/buckets/media` 与 `/app/buckets/images` 不存在；尚未对所有桶内容做媒体递归盘点 |
| Zeabur 内网暴露 | 网络页面没有配置项目内网暴露端口 |

### 项目内服务辨识

同一 Zeabur 项目当前可见四个服务：

| 服务 | 当前用途 | 公开地址 | 本次处理 |
| --- | --- | --- | --- |
| `xinchao-nian-caric` | 生产心潮 | `xinchao-nanzhi.zeabur.app` | 未修改、未重启、未重新部署 |
| `ombre-brain` | 原版 OB | 未在本次 vNext 核查中操作 | 未修改 |
| `ombre-brain-haven` | Haven 旁支 | 未在本次 vNext 核查中操作 | 未修改 |
| `ombre-brain-vnext` | 隔离 OB 实验服务 | `ombre-brain-vnext.zeabur.app` | 只读核查 |

这一区分很重要：不能把项目里现有的 `xinchao-nian-caric` 当作 vNext
心潮，也不能把 `ombre-brain-vnext` 的 OB 公网地址交给手机或桌面 MCP
客户端作为统一入口。

终端诊断只读取了进程命令行、监听表、挂载元数据、文件类型和大小，没有读取桶 Markdown 正文、媒体内容或环境变量值。

## 本地代码已确认事实

根目录 `Dockerfile` 是独立心潮镜像：

- 基于 `node:20-alpine`；
- 启动 `node src/server.js`；
- 暴露 `18110`；
- 持久化目录只有 `/app/state`；
- 通过 `OMBRE_MCP_URL` 连接外部 OB；
- 不复制 `ombre-brain/`，不启动 Python OB 进程。

本地 `compose.yaml` 才是联合部署结构：

```text
ombre-brain       Python OB，容器内 8000，单一 MCP 路由 /mcp
dynamic-mind      Node 心潮，容器内 18110，对外统一 MCP 路由 /mcp
xinchao-state     心潮 state.json、continuity、journal、OAuth 等
ombre-buckets     OB config、Markdown 桶、embeddings.db、outbox、媒体与派生文件
```

联合 Compose 中，心潮通过 `http://ombre-brain:8000/mcp` 访问 OB；对外连接器应指向心潮的 `18110/mcp`，而不是 OB 的管理端口。根 `Dockerfile` 与这份 Compose 是两种不同部署模式，不能混用推断。

## 当前阻断项

以下关键事实已经确认，因此不再把它们当作未知项；在剩余事项确认前，仍不进行重新部署、服务切换、卷操作或数据导入：

1. 当前服务没有心潮进程、心潮 state 卷或心潮 `18110` 入口；需要另建隔离心潮服务，不能靠当前服务自动补齐联合部署。
2. vNext OB 的实际配置文件内容与 `buckets_dir` 解析结果；目前只确认 `config.yaml` 存在，不读取配置正文。
3. 是否需要把 OB 端口通过 Zeabur 项目内网暴露给未来的 vNext 心潮服务。
4. 独立 vNext 心潮服务的 `OMBRE_MCP_URL`、独立 token、`MCP_ENABLED`、state 卷和公开统一入口。
5. 双服务之间的 MCP `initialize`、`tools/list`、会话刷新和鉴权行为。
6. 导入测试数据副本后的桶、媒体、embedding、摘要检索和原对话追溯验收结果。

新增隔离心潮服务前，必须先在 Zeabur 为它准备独立的 `/app/state` 卷和
独立变量。不能复制生产服务的整组环境变量；尤其是 `SERVICE_TOKEN`、
`OMBRE_MCP_TOKEN`、`OAUTH_APPROVAL_TOKEN`、Dashboard 口令和任何生产
`OMBRE_MCP_URL`。

现在已经可以确认：这个服务就是只运行 OB 的实验服务。卷名称与卷内容也已核实，但它仍然不能充当心潮统一 MCP 入口。

## 推荐的隔离目标拓扑

验收期间保留生产心潮和原版 OB 不动，vNext 使用独立资源：

```text
手机 MCP ─┐
桌面 MCP ─┴─> vNext 心潮 HTTPS /mcp
                    │ 内部网络 + 独立 token
                    v
              vNext OB /mcp
                    │
              vNext 独立 OB 卷
```

若 Zeabur 单服务不能稳定运行两个进程，应拆成两个隔离服务：

- `ombre-brain-vnext-ob`：只运行 OB，挂载 `ombre-vnext-buckets`；
- `ombre-brain-vnext-xinchao`：只运行心潮，挂载独立 state 卷，将 `OMBRE_MCP_URL` 指向上面的 OB 内网地址；
- 只有心潮服务暴露公开 MCP 入口；OB 只允许内部访问或单独保护的 Dashboard。

无论采用单服务还是双服务，生产心潮的 OB 地址、生产 MCP、原版 OB 和生产数据卷都不得出现在 vNext 的运行配置中。

## 联合验收门槛

按以下顺序验收，前一项未通过不进入后一项：

1. 两端 MCP `initialize` 成功，协议版本和会话行为稳定；
2. 心潮 `tools/list` 同时暴露心潮工具和允许代理的 OB 工具；OB 暂时不可达时，心潮工具仍能返回，恢复后工具缓存能刷新；
3. 独立测试数据中验证 `breath`、`hold`、`feel`、`trace`、`pulse` 和图片 URL 下载；
4. 验证梦境重复排除、确认写入、`dont_surface` 和跨端最近上下文；
5. 检查桶 Markdown 是真源，`embeddings.db`、outbox 和媒体文件在重启后仍存在；
6. 在手机端和桌面端分别重复 `initialize`、`tools/list`、`breath`、`hold`、`feel`、Miss、dream 和连续性流程；
7. 记录服务重启、OB 重启、心潮重启后的 MCP session 刷新结果；
8. 形成验收报告后，才讨论任何生产迁移方案。

## 本地验证记录

- 心潮在 `xinchao/` 执行 `npm test`：64 项通过。
- OB 在 `ombre-brain/` 设置 `PYTHONPATH=src` 后执行 `python -m pytest -q`：12 项通过。零 TTL 重试边界已修复并通过复跑。
- 本机没有 Docker CLI，因此未执行 `docker compose config` 或容器启动验证。
- 当前分支：`codex/cross-client-continuity`。
- 当前提交基线：`dc8676e Update Ombre compatibility note`。
- Zeabur 只读核查：vNext 服务设置的构建上下文为 `/ombre-brain`，Dockerfile
  覆盖为空；网络页没有内网暴露；`ombre-vnext-buckets` 挂载到
  `/app/buckets`，用量约 27.68 MB；生产心潮服务仍为
  `xinchao-nian-caric`，未被本次操作触碰。
