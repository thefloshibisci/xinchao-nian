# MODIFICATIONS — 心潮念对 Ombre Brain 的修改

本目录的 Ombre Brain 源码是**衍生版本**，血统与许可证：

```
P0luz/Ombre-Brain（原项目, MIT, © P0lar1zzZ）
  → CyberSealNull 二改（在原 MIT 之上追加"新增内容非商业"约束）
    → 心潮念（本仓库）在其基础上做了下面的修改
```

- 原项目：https://github.com/P0luz/Ombre-Brain  （MIT，见 `LICENSE.P0luz-MIT`）
- 二改仓库：CyberSealNull（fork of P0luz，见 `LICENSE.CyberSealNull` / `NOTICE.CyberSealNull.md`；
  其新增内容仅限个人 / 学习 / 非商业，商用需该 fork 维护者书面许可）
- 本版本基线：`VERSION` = 2.6.6

## 心潮念在此基础上的改动

1. **breath-meta（记忆共振依赖）**
   - `src/tools/breath/_verbatim.py`：`breath` 返回时，在每条桶的表头带上结构化
     `[domain:…] [tags:…]`（新增 `_affinity_meta()`）。
   - 目的：让上层的"心潮"动态心智直接按 domain 算亲和度、把浮现的记忆回推到驱力
     （记忆共振），不用猜关键词。additive、向后兼容——老输出没有该表头时按空处理。

2. **压缩/脱水模型默认**（部署配置层，非源码逻辑）
   - 压缩模型从 GLM-Z1 换成 DeepSeek-V3（11.5s → ~1.0s）。通过 config / 环境变量配置，
     不改源码。

3. **grow 超时重试防重复**
   - `src/tools/grow/retry_guard.py`：同一份 grow 请求在短时间内只运行一次；客户端超时或
     断开不会取消已经开始的写入，进行中重试会收到状态提示，完成后重试会复用原结果。
   - 指纹额外纳入二改版的 `auto/source` 写入门卫参数，人工写入与后台候选不会被误判为
     同一请求；真实失败不缓存，仍可正常重试。短内容自动候选的门卫判断也位于防重边界内，
     暂缓/拒绝结果只短暂冷却，避免网络重试虚增候选出现次数，同时不长期锁住真实复现。

4. **向量检索稳定性回移植**
   - 吸收新版 OB 的内存边界与 Gemini 鉴权修复：检索按小批次计算、缓存键有界，且原生
     Gemini embedding 不再把 API key 放进 URL。保持现有 `meaning` 双向量与心潮客户端协议不变。

5. **分拆 recall 工具兼容层**
   - 在保留二改版统一参数化 `breath` 的同时，新增公开 `breath_search` 与
     `breath_advanced` 薄封装；两者复用同一套检索、过滤、内存保护和逐字返回实现，不复制数据路径。
   - 目的：兼容新版 OB 客户端的工具发现与回退顺序，同时不破坏已有客户端和迁移验收脚本。
   - 不恢复新版已删除的 `source_read`；迁移冷档案继续使用完整 bucket ID 的受控定向检索。

6. **媒体二进制持久化与可验证备份恢复**
   - 从新版原版 OB 回移植 `MediaStore`：`data_base64`/受限临时路径会在写 bucket 前复制到
     持久媒体目录，使用 SHA-256 内容寻址、原子写入、批次失败回滚与已有目标哈希校验。
   - `OMBRE_MEDIA_DIR` 可指定独立持久卷；默认 `<buckets_dir>/_media`。
     `OMBRE_MEDIA_MAX_BYTES` 控制单项上限，默认 25 MiB；单次仍最多 20 项。
   - 导出包把媒体放在独立的 `media/` 前缀并纳入完整性 manifest；恢复时校验大小、文件名哈希、
     `stored=true + sha256` 引用闭包，再按当前环境媒体目录恢复并重写稳定路径。
   - 旧版仅含历史 `path`、没有二进制成员的包仍可解析，但不会被误报为媒体二进制已恢复。

> OB 原生功能全部保留：breath / hold / grow / dream / trace / anchor / release / forget /
> restore / purge / I / plan / letter / pulse 与 Dashboard。心潮念只做上述增量，不裁剪。

## 合并后整体许可证

心潮念整体**非纯 MIT**：`ombre-brain/` 部分受 CyberSealNull 二改的非商业约束 + P0luz 原 MIT 的
署名/许可保留要求约束。商业使用需取得上游（P0luz 及 CyberSealNull fork 维护者）的书面许可。
详见仓库根 `NOTICE`。
