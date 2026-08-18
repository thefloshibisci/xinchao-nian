export class OmbreClient {
  constructor(config) {
    this.config = config;
    this.sessionId = null;
    this.initializePromise = null;
    this.toolsCache = null;
    this.toolsPromise = null;
    this.memoryMetadataCache = null;
    this.memoryMetadataPromise = null;
  }

  async post(payload, expectBody = true) {
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      'X-Ombre-Caller': 'dynamic-mind',
    };
    if (this.config.token) headers.Authorization = `Bearer ${this.config.token}`;
    if (this.sessionId) headers['Mcp-Session-Id'] = this.sessionId;
    const response = await fetch(this.config.url, {
      method: 'POST', headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok) throw new Error(`Ombre MCP failed: HTTP ${response.status}`);
    this.sessionId = response.headers.get('mcp-session-id') ?? this.sessionId;
    if (!expectBody) return null;
    const text = await response.text();
    return text ? parseMcp(text) : null;
  }

  async initialize() {
    if (this.sessionId || this._initialized) return;
    if (!this.initializePromise) {
      this.initializePromise = (async () => {
        await this.post({
          jsonrpc: '2.0',
          id: Date.now(),
          method: 'initialize',
          params: {
            protocolVersion: '2025-06-18',
            capabilities: {},
            clientInfo: { name: 'xinchao-dynamic-mind', version: '2.4.0' },
          },
        });
        // OB 1.28.x does not return Mcp-Session-Id; proceed sessionless.
        this._initialized = true;
        await this.post({ jsonrpc: '2.0', method: 'notifications/initialized' }, false);
      })().finally(() => { this.initializePromise = null; });
    }
    return this.initializePromise;
  }

  async call(name, args = {}) {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await this.initialize();
      try {
        return await this.post({ jsonrpc: '2.0', id: Date.now(), method: 'tools/call', params: { name, arguments: args } });
      } catch (error) {
        if (attempt || !/HTTP (400|404)/.test(error.message)) throw error;
        this.sessionId = null;
      }
    }
    throw new Error('Ombre MCP call failed after session refresh');
  }

  // 网关用：拉 OB 的 tools/list（供心潮念合并暴露 OB 记忆工具）。
  // 带会话刷新重试——OB 重启后旧 session 失效，第一次会失败；不重试的话 tools/list 会
  // 瞬态只剩心潮 3 个工具（OB 工具消失），直到下次拉取。tools/list 只读、重试安全。
  async listTools({ refresh = false } = {}) {
    if (!refresh && this.toolsCache) return this.toolsCache;
    if (!refresh && this.toolsPromise) return this.toolsPromise;
    this.toolsPromise = (async () => {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          await this.initialize();
          const raw = await this.post({ jsonrpc: '2.0', id: Date.now(), method: 'tools/list', params: {} });
          const tools = raw?.result?.tools ?? raw?.tools ?? [];
          this.toolsCache = Array.isArray(tools) ? tools : [];
          return this.toolsCache;
        } catch (error) {
          this.sessionId = null;
          if (attempt) throw error;
        }
      }
      return [];
    })().finally(() => { this.toolsPromise = null; });
    return this.toolsPromise;
  }

  async toolInfo(name) {
    return (await this.listTools()).find((tool) => tool?.name === name) ?? null;
  }

  // Ombre Brain 2.6.x used a parameterised `breath`; newer releases split it
  // into zero-argument breath + breath_search + breath_advanced. Discover the
  // remote schema instead of pinning Xinchao to either OB release line.
  async callBreath({ query, maxResults, maxTokens }) {
    const tools = await this.listTools();
    const names = new Set(tools.map((tool) => tool?.name));
    if (names.has('breath_advanced')) {
      return this.call('breath_advanced', {
        query,
        max_results: maxResults,
        max_tokens: maxTokens,
      });
    }
    if (names.has('breath_search')) {
      return this.call('breath_search', { query, max_results: maxResults });
    }
    return this.call('breath', { query, max_results: maxResults, max_tokens: maxTokens });
  }

  async recentMaterial(drives = []) {
    const result = await this.callBreath({
      query: withDriveHint('近期重要记忆、情绪、关系变化和未完成事项', drives),
      maxResults: this.config.breathMaxResults,
      maxTokens: this.config.breathMaxTokens
    });
    return extractText(result).slice(0, 10000);
  }

  // 做梦使用独立的、可追踪的召回。固定 query 会让 OB 的稳定排序连续几天
  // 返回同一批高分桶；这里按近期梦的数量轮换观察角度，并且仅在首轮素材
  // 全部已被近三次梦使用时，换一个角度再读一次。重试有且只有一次。
  async dreamMaterial(drives = [], recentDreams = []) {
    const usedIds = recentDreamSourceIds(recentDreams);
    const start = Math.max(0, Array.isArray(recentDreams) ? recentDreams.length : 0) % DREAM_MATERIAL_ANGLES.length;
    const first = await this.recallDreamMaterial(DREAM_MATERIAL_ANGLES[start], drives);
    let selected = first;

    if (first.bucketIds.length && first.bucketIds.every((id) => usedIds.has(id))) {
      const fallback = await this.recallDreamMaterial(
        DREAM_MATERIAL_ANGLES[(start + 1) % DREAM_MATERIAL_ANGLES.length],
        drives,
      );
      if (fallback.bucketIds.some((id) => !usedIds.has(id))) selected = fallback;
    }

    return {
      text: selected.text,
      bucketIds: selected.bucketIds,
      skippedBucketIds: selected.bucketIds.filter((id) => usedIds.has(id)),
    };
  }

  async recallDreamMaterial(angle, drives = []) {
    const result = await this.callBreath({
      query: withDriveHint(`${angle}；优先具体的人、话、场景和身体感受，不要返回系统配置、部署或技术信息`, drives),
      maxResults: this.config.breathMaxResults,
      maxTokens: this.config.breathMaxTokens,
    });
    const text = extractText(result).slice(0, 10000);
    return { text, bucketIds: parseSurfacedBucketIds(text) };
  }

  async daytimeMaterial(drives = []) {
    const result = await this.callBreath({
      query: withDriveHint('白天自然浮现的近期记忆、具体细节、未说完的话和当下牵挂；不要返回系统配置或技术信息', drives),
      maxResults: this.config.breathMaxResults,
      maxTokens: this.config.breathMaxTokens
    });
    return extractText(result).slice(0, 10000);
  }

  // 自主念头用的材料：比日间浮现更短，只要能让念头落到具体的事上。
  async thoughtMaterial(drives = []) {
    const result = await this.callBreath({
      query: withDriveHint('此刻自然想起的一件具体的事：最近的共同经历、说过的话或还惦记着的东西；不要返回系统配置、部署或技术信息', drives),
      maxResults: Math.max(1, Math.min(3, Number(this.config.breathMaxResults) || 2)),
      maxTokens: Math.max(200, Math.min(600, Number(this.config.breathMaxTokens) || 400))
    });
    return extractText(result).slice(0, 4000);
  }

  async recentContinuityMaterial(maxTokens = this.config.breathMaxTokens) {
    const result = await this.callBreath({
      query: [
        '新窗口近期连续性：只返回最近发生了什么，以及仍直接影响现在的人物与关系变化、生活重点和未完成约定。',
        '不要返回核心准则、自我基岩或长期画像；这些由客户端从自己的核心指令和长期记忆单独完整读取。',
        '不要返回部署、代码、接口、密钥、系统日志或已经过期的技术待办。',
      ].join(''),
      maxResults: Math.max(3, Math.min(8, Number(this.config.breathMaxResults) || 3)),
      maxTokens: Math.max(200, Math.min(3000, Number(maxTokens) || 1600)),
    });
    return extractText(result).slice(0, 16000);
  }

  // Compatibility alias for older callers.  It intentionally returns only
  // recent continuity; it is not a replacement for repository bedrock.
  async handoffMaterial(maxTokens = this.config.breathMaxTokens) {
    return this.recentContinuityMaterial(maxTokens);
  }

  // 网页记忆星图只读取 pulse 暴露的桶元数据，不读取正文。公开版当前的
  // pulse 是人类可读文本；未来融合版若直接返回结构化 JSON，同一适配器也
  // 会保留 driveSnapshot / driveAffinity 等 3.0 可选字段。
  async memoryMap() {
    if (!this.config.readEnabled) return emptyMemoryMap('not_configured');
    const result = await this.call('pulse', {});
    return parseMemoryMapText(extractText(result));
  }

  async memoryMetadata({ refresh = false } = {}) {
    const now = Date.now();
    if (!refresh && this.memoryMetadataCache && now < this.memoryMetadataCache.expiresAt) {
      return this.memoryMetadataCache.map;
    }
    if (!refresh && this.memoryMetadataPromise) return this.memoryMetadataPromise;
    this.memoryMetadataPromise = (async () => {
      const map = await this.memoryMap();
      const byId = new Map((map.stars ?? []).map((star) => [String(star.id), star]));
      this.memoryMetadataCache = { map: byId, expiresAt: Date.now() + 5 * 60_000 };
      return byId;
    })().finally(() => { this.memoryMetadataPromise = null; });
    return this.memoryMetadataPromise;
  }

  // 星图列表继续只含元数据；用户点开某颗星时才按完整 bucket id 读取一次。
  // id 必须先存在于 pulse 元数据中，避免把 Dashboard 变成任意 breath 查询代理。
  async memoryDetail(bucketId) {
    if (!this.config.readEnabled) return { available: false, reason: 'not_configured' };
    const id = String(bucketId ?? '').trim();
    if (!MEMORY_BUCKET_ID_RE.test(id)) return { available: false, reason: 'invalid_id' };

    let byId = await this.memoryMetadata();
    if (!byId.has(id)) byId = await this.memoryMetadata({ refresh: true });
    const star = byId.get(id);
    if (!star) return { available: false, reason: 'not_found' };

    const result = await this.callBreath({
      query: id,
      maxResults: 1,
      maxTokens: Math.max(6000, Math.min(12000, Number(this.config.breathMaxTokens) || 6000)),
    });
    const surfaced = extractText(result);
    if (!parseSurfacedBucketIds(surfaced).includes(id)) {
      return { available: false, reason: 'not_found' };
    }
    const preview = memoryPreview(surfaced, id);
    if (!preview) return { available: false, reason: 'empty' };
    return { available: true, id, preview, star };
  }

  async surfacedMetadata(text) {
    const bucketIds = parseSurfacedBucketIds(text);
    const legacyDomains = parseSurfacedDomains(text);
    if (!bucketIds.length) return { bucketIds, domains: legacyDomains, tags: [] };
    let byId;
    try { byId = await this.memoryMetadata(); }
    catch { return { bucketIds, domains: legacyDomains, tags: [] }; }
    const domains = [...legacyDomains];
    const tags = [];
    for (const id of bucketIds) {
      const star = byId.get(id);
      if (!star) continue;
      domains.push(...(star.domains ?? []));
      tags.push(...(star.tags ?? []));
    }
    return {
      bucketIds,
      domains: [...new Set(domains.map(String).map((value) => value.trim()).filter(Boolean))],
      tags: [...new Set(tags.map(String).map((value) => value.trim()).filter(Boolean))],
    };
  }

  async storeDream(dream, { confirmed = false } = {}) {
    if (!confirmed && !this.config.writeEnabled) return null;
    const content = [
      `我梦见：${dream.dream}`,
      `我醒来后仍有这样的余韵：${dream.residue}`,
      `我醒后的意识是：${dream.awareness}`,
      '我知道这是睡眠结算产生的梦境，不是现实事件；调用外部记忆服务也不等于我醒来。'
    ].join('\n');
    const holdTool = await this.toolInfo('hold');
    const supported = new Set(Object.keys(holdTool?.inputSchema?.properties ?? {}));
    const candidates = {
      content,
      title: `确认保存的心潮梦境 ${String(dream.createdAt ?? '').slice(0, 10)}`.trim(),
      tags: '梦境,心潮,非现实',
      importance: 6,
      feel: true,
      why_remembered: confirmed ? '严槿与我明确确认，这个梦值得作为梦境而不是现实事件留下。' : '心潮自动保存的梦境。',
      meaning: '这是睡眠结算产生的梦境记录，不是现实事件，也不应被当作事实依据。',
      auto: true,
      source: 'xinchao-dream',
    };
    // Keep compatibility with current OB, whose public hold no longer accepts
    // the legacy auto/source fields. Unknown fields are never sent.
    const args = supported.size
      ? Object.fromEntries(Object.entries(candidates).filter(([key]) => supported.has(key)))
      : candidates;
    const result = await this.call('hold', args);
    const text = extractText(result);
    const bucketId = text.match(/[a-f0-9]{12,}/i)?.[0] ?? null;
    // 梦是睡眠结算的残渣，不该作为真实记忆回到 breath（否则下次梦引擎会把旧梦当素材捞出 → 梦吃梦）。
    // 出生即标 dont_surface=1：仍存在 OB、仍显示在梦境页（来自心潮 state），但不进 breath 召回。
    if (bucketId) {
      try { await this.call('trace', { bucket_id: bucketId, dont_surface: 1 }); }
      catch (error) { /* best-effort：标记失败不阻断存梦本身 */ }
    }
    return bucketId;
  }
}

// 把当前最强的几个驱动力拼进 breath 的 query，让"此刻想什么"影响"想起什么"。
//
// 这里只改排序，不改准入：能不能返回仍然由 Ombre 的 admission gate 判定
// （要有原句、词锚或高语义证据）。所以驱动力高不会凭空造出记忆，只会让
// 本来就有证据的那几条里，跟当下状态相关的先浮上来。末尾那句兜底很重要，
// 没有它的话强驱动力会把召回卡死成空。
function withDriveHint(base, drives) {
  const labels = (Array.isArray(drives) ? drives : [])
    .filter((item) => Number(item?.value) >= DRIVE_HINT_MIN)
    .slice(0, DRIVE_HINT_MAX_LABELS)
    .map((item) => String(item?.label ?? '').trim())
    .filter(Boolean);
  if (!labels.length) return base;
  return `${base}。此刻最强的内在状态是${labels.join('、')}，优先浮现与之真正相关的具体记忆；没有直接相关的就照常返回近期重要的`;
}

const DRIVE_HINT_MIN = 0.5;
const DRIVE_HINT_MAX_LABELS = 3;
const MEMORY_BUCKET_ID_RE = /^[A-Za-z0-9._-]{1,160}$/;
const DREAM_MATERIAL_ANGLES = [
  '最近新增或更新的共同经历、生活细节，以及还没有进入近期梦境的记忆',
  '最近没说完的话、关系里的细微变化和当下真正牵挂的事',
  '较久没有浮现、但仍在影响现在的具体记忆和感受',
];

function recentDreamSourceIds(recentDreams) {
  const ids = (Array.isArray(recentDreams) ? recentDreams : [])
    .slice(-3)
    .flatMap((dream) => Array.isArray(dream?.sourceMemoryIds) ? dream.sourceMemoryIds : []);
  return new Set(ids.map(String).filter((id) => MEMORY_BUCKET_ID_RE.test(id)));
}

// 从 breath 输出里把每条桶表头的 [domain:...] 解析出来，供记忆共振算亲和度。
// OB 2.6.5+（breath-meta）在表头带 domain/tags；老输出没有时返回空数组，不影响。
export function parseSurfacedBucketIds(text) {
  const ids = [];
  const re = /\[bucket_id:([A-Za-z0-9._-]{1,160})\]/g;
  let match;
  while ((match = re.exec(String(text ?? ''))) !== null) ids.push(match[1]);
  return [...new Set(ids)];
}

// breath exact-id 输出的首行是读取元数据，正文从下一行开始。星图只需要
// 一小段预览；已知的尾部检索提示不应混进用户的记忆正文。
export function memoryPreview(text, bucketId) {
  const id = String(bucketId ?? '').trim();
  const lines = String(text ?? '').replace(/\r\n/g, '\n').split('\n');
  const header = lines.findIndex((line) => line.includes(`[bucket_id:${id}]`));
  if (header < 0) return '';
  const body = lines.slice(header + 1);
  const metadataTail = body.findIndex((line) => (
    /^\[(?:source_available|relation_hint|related_bucket)/.test(line.trim())
    || /^👣\s*Footprint/.test(line.trim())
    || (line.includes('[bucket_id:') && !line.includes(`[bucket_id:${id}]`))
  ));
  const contentLines = metadataTail >= 0 ? body.slice(0, metadataTail) : body;
  while (contentLines.at(-1)?.trim() === '---') contentLines.pop();
  const content = contentLines.join('\n').trim();
  return Array.from(content).slice(0, 6000).join('');
}

export function parseSurfacedDomains(text) {
  const domains = [];
  const re = /\[domain:([^\]]*)\]/g;
  let match;
  while ((match = re.exec(String(text ?? ''))) !== null) {
    for (const part of match[1].split(',')) {
      const value = part.trim();
      if (value) domains.push(value);
    }
  }
  return [...new Set(domains)];
}

function parseMcp(text) {
  const data = text.split('\n').find((line) => line.startsWith('data:'))?.slice(5).trim() ?? text;
  return JSON.parse(data);
}

function extractText(result) {
  const content = result?.result?.content ?? result?.content ?? [];
  return content.filter((part) => part.type === 'text').map((part) => part.text).join('\n');
}

function emptyMemoryMap(reason = 'empty') {
  return {
    schemaVersion: 2,
    generatedAt: new Date().toISOString(),
    available: false,
    reason,
    total: 0,
    stats: {},
    stars: [],
    edges: [],
    capabilities: {
      explicitRelations: false,
      driveSnapshots: false,
      driveAffinity: false,
      timestamps: false,
    },
  };
}

function numberOrNull(value) {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeStar(star = {}) {
  const id = String(star.id ?? star.bucketId ?? star.bucket_id ?? '').trim();
  if (!id) return null;
  const pinned = Boolean(star.pinned || star.bucketType === 'permanent' || star.type === 'permanent');
  const driveSnapshot = star.driveSnapshot ?? star.drive_snapshot ?? null;
  const driveAffinity = star.driveAffinity ?? star.drive_affinity ?? null;
  return {
    id,
    title: String(star.title ?? star.name ?? '（无题）').trim() || '（无题）',
    pinned,
    bucketType: String(star.bucketType ?? star.type ?? (pinned ? 'permanent' : 'dynamic')),
    domains: Array.isArray(star.domains) ? star.domains.map(String).filter(Boolean)
      : Array.isArray(star.domain) ? star.domain.map(String).filter(Boolean)
        : String(star.domain ?? '').split(/[,，]/).map((item) => item.trim()).filter(Boolean),
    valence: numberOrNull(star.valence),
    arousal: numberOrNull(star.arousal),
    importance: numberOrNull(star.importance),
    weight: numberOrNull(star.weight ?? star.score),
    tags: Array.isArray(star.tags) ? star.tags.map(String).filter(Boolean)
      : String(star.tags ?? '').split(/[,，]/).map((item) => item.trim()).filter(Boolean),
    createdAt: star.createdAt ?? star.created_at ?? null,
    updatedAt: star.updatedAt ?? star.updated_at ?? null,
    lastActiveAt: star.lastActiveAt ?? star.last_active ?? null,
    activationCount: numberOrNull(star.activationCount ?? star.activation_count),
    anchored: Boolean(star.anchored),
    resolved: Boolean(star.resolved),
    historical: star.historical == null ? !driveSnapshot : Boolean(star.historical),
    meaningCount: Array.isArray(star.meaning) ? star.meaning.length : Number(star.meaningCount ?? 0) || 0,
    driveSnapshot: driveSnapshot && typeof driveSnapshot === 'object' ? driveSnapshot : null,
    driveAffinity: driveAffinity && typeof driveAffinity === 'object' ? driveAffinity : null,
  };
}

function buildMapEdges(stars, minShared = 3, maxPerNode = 6) {
  const byTag = new Map();
  stars.forEach((star, index) => star.tags.forEach((tag) => {
    if (!byTag.has(tag)) byTag.set(tag, []);
    byTag.get(tag).push(index);
  }));
  const pairs = new Map();
  for (const indexes of byTag.values()) {
    if (indexes.length > stars.length * .5) continue;
    for (let left = 0; left < indexes.length; left += 1) {
      for (let right = left + 1; right < indexes.length; right += 1) {
        const key = `${indexes[left]}|${indexes[right]}`;
        pairs.set(key, (pairs.get(key) || 0) + 1);
      }
    }
  }
  const candidates = [];
  for (const [key, shared] of pairs) {
    if (shared < minShared) continue;
    const [left, right] = key.split('|').map(Number);
    const denominator = Math.min(stars[left].tags.length, stars[right].tags.length) || 1;
    candidates.push({ left, right, shared, similarity: Math.min(1, shared / denominator) });
  }
  candidates.sort((a, b) => b.similarity - a.similarity || b.shared - a.shared);
  const degree = new Array(stars.length).fill(0);
  const edges = [];
  for (const candidate of candidates) {
    if (degree[candidate.left] >= maxPerNode || degree[candidate.right] >= maxPerNode) continue;
    degree[candidate.left] += 1;
    degree[candidate.right] += 1;
    edges.push({
      source: stars[candidate.left].id,
      target: stars[candidate.right].id,
      similarity: Number(candidate.similarity.toFixed(2)),
      kind: 'tag-derived',
      label: `${candidate.shared} 个共同标签`,
    });
  }
  return edges;
}

function normalizeEdges(edges, stars) {
  const ids = new Set(stars.map((star) => star.id));
  return (Array.isArray(edges) ? edges : []).flatMap((edge) => {
    const source = String(edge?.source ?? '').trim();
    const target = String(edge?.target ?? '').trim();
    if (!source || !target || source === target || !ids.has(source) || !ids.has(target)) return [];
    return [{
      source,
      target,
      similarity: Math.max(0, Math.min(1, Number(edge.similarity ?? edge.weight ?? 0) || 0)),
      kind: edge.kind === 'semantic' || edge.kind === 'tag-derived' ? edge.kind : 'explicit',
      label: String(edge.label ?? '').slice(0, 120),
    }];
  });
}

export function parseMemoryMapText(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return emptyMemoryMap('empty');

  // 3.0 结构化输出优先；公开版旧 pulse 继续走下方无损文本适配。
  try {
    const parsed = JSON.parse(text);
    const sourceStars = parsed.stars ?? parsed.nodes;
    if (Array.isArray(sourceStars)) {
      const stars = sourceStars.map(normalizeStar).filter(Boolean);
      const explicitEdges = normalizeEdges(parsed.edges ?? parsed.links, stars);
      const capabilities = {
        explicitRelations: explicitEdges.length > 0,
        driveSnapshots: stars.some((star) => star.driveSnapshot),
        driveAffinity: stars.some((star) => star.driveAffinity),
        timestamps: stars.some((star) => star.createdAt || star.updatedAt),
      };
      return {
        schemaVersion: Number(parsed.schemaVersion ?? 2),
        generatedAt: String(parsed.generatedAt ?? new Date().toISOString()),
        available: true,
        total: stars.length,
        stats: parsed.stats && typeof parsed.stats === 'object' ? parsed.stats : {},
        stars,
        edges: explicitEdges.length ? explicitEdges : buildMapEdges(stars),
        capabilities,
      };
    }
  } catch {
    // 人类可读 pulse 不是 JSON，继续解析；不把解析失败当服务故障。
  }

  const stats = {};
  for (const [key, label] of [['pinned', '固化桶'], ['dynamic', '动态桶'], ['archived', '归档桶']]) {
    const match = text.match(new RegExp(`${label}[:：]\\s*(\\d+)`));
    if (match) stats[key] = Number(match[1]);
  }
  const size = text.match(/总占用[:：]\s*([\d.]+\s*\w+)/);
  if (size) stats.size = size[1];

  const stars = [];
  const line = /((?:\uD83D\uDCCC)?)\s*\[([0-9a-f]+)\]\s*《([^》]*)》([^\n]*)/gi;
  let match;
  while ((match = line.exec(text)) !== null) {
    const [, pin, id, title, tail] = match;
    const domain = (tail.match(/主题[:：]\s*([^\s]+)/) || [])[1] || '';
    const emotion = tail.match(/情感[:：]\s*V(-?[\d.]+)\/A(-?[\d.]+)/);
    const importance = (tail.match(/重要[:：]\s*([\d.]+)/) || [])[1];
    const weight = (tail.match(/权重[:：]\s*([\d.]+)/) || [])[1];
    const tags = (tail.match(/标签[:：]\s*(.+)$/) || [])[1] || '';
    stars.push(normalizeStar({
      id,
      title,
      pinned: pin.length > 0,
      bucketType: pin.length > 0 ? 'permanent' : 'dynamic',
      domains: domain.split(/[,，]/).filter(Boolean),
      valence: emotion ? Number(emotion[1]) : null,
      arousal: emotion ? Number(emotion[2]) : null,
      importance: importance ? Number(importance) : null,
      weight: weight ? Number(weight) : null,
      tags: tags.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
      historical: true,
    }));
  }
  const filteredStars = stars.filter(Boolean);
  return {
    schemaVersion: 2,
    generatedAt: new Date().toISOString(),
    available: true,
    total: filteredStars.length,
    stats,
    stars: filteredStars,
    edges: buildMapEdges(filteredStars),
    capabilities: {
      explicitRelations: false,
      driveSnapshots: false,
      driveAffinity: false,
      timestamps: false,
    },
  };
}

