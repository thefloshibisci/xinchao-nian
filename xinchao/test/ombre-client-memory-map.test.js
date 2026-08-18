import assert from 'node:assert/strict';
import test from 'node:test';

import { OmbreClient, memoryPreview, parseMemoryMapText } from '../src/ombre-client.js';

test('pulse text becomes a metadata-only memory map', () => {
  const result = parseMemoryMapText(`
固化桶：2
动态桶：4
归档桶：1
总占用：2.5 MB
📌 [core123] 《钉住核心》 主题:恋爱,成长 情感:V0.9/A0.7 重要:10 权重:80 标签:共同记忆,承诺,成长
📦 [long456] 《普通长期》 主题:恋爱 情感:V0.8/A0.4 重要:8 权重:45 标签:共同记忆,承诺,成长
🫧 [feel_202608181030_V085] 《一阵心潮》 主题:生活 情感:V0.5/A0.8 重要:7 权重:30 标签:感受,晚风
📋 [plan777] 《明天的计划》 主题:生活 情感:V0.6/A0.3 重要:5 权重:18 标签:计划,安排
💌 [letter777] 《没有寄出的信》 主题:恋爱 情感:V0.7/A0.6 重要:6 权重:24 标签:信件,心事
💭 [live789] 《动态记忆》 主题:生活 情感:V0.5/A0.2 重要:4 权重:12 标签:吃饭,天气,通勤
🗄️ [old999] 《归档记忆》 主题:工作 情感:V0.4/A0.5 重要:3 权重:9 标签:项目,会议,进度
✅ [done888] 《已经解决》 [已解决] 主题:生活 情感:V0.6/A0.3 重要:2 权重:5 标签:琐事
`);

  assert.equal(result.available, true);
  assert.equal(result.total, 8);
  assert.equal(result.stats.pinned, 2);
  assert.equal(result.stars[0].pinned, true);
  assert.equal(result.stars[0].bucketType, 'permanent');
  assert.equal(result.stars[1].pinned, false);
  assert.equal(result.stars[1].bucketType, 'permanent');
  assert.equal(result.stars[2].id, 'feel_202608181030_V085');
  assert.equal(result.stars[2].bucketType, 'feel');
  assert.equal(result.stars[3].bucketType, 'plan');
  assert.equal(result.stars[4].bucketType, 'letter');
  assert.equal(result.stars[5].bucketType, 'dynamic');
  assert.equal(result.stars[6].bucketType, 'archive');
  assert.equal(result.stars[7].resolved, true);
  assert.equal(result.stars[0].driveSnapshot, null);
  assert.equal(result.stars[0].historical, true);
  assert.equal(result.edges.length, 1);
  assert.equal(result.edges[0].kind, 'tag-derived');
  assert.equal('content' in result.stars[0], false);
});

test('structured 3.0 map preserves optional emotional stamp fields', () => {
  const result = parseMemoryMapText(JSON.stringify({
    schemaVersion: 3,
    stars: [{
      id: 'future-bucket',
      title: '带情感戳的瞬间',
      domain: ['恋爱'],
      importance: 9,
      valence: .72,
      arousal: .84,
      drive_snapshot: { possess: .8 },
      drive_affinity: { possess: .9 },
      created_at: '2026-08-09T10:00:00.000Z',
    }, {
      id: 'ordinary-permanent',
      title: '没有钉住的普通长期记忆',
      type: 'permanent',
      pinned: false,
    }],
    edges: [],
  }));

  assert.equal(result.available, true);
  assert.equal(result.stars[0].historical, false);
  assert.deepEqual(result.stars[0].driveSnapshot, { possess: .8 });
  assert.deepEqual(result.stars[0].driveAffinity, { possess: .9 });
  assert.equal(result.stars[1].bucketType, 'permanent');
  assert.equal(result.stars[1].pinned, false);
  assert.equal(result.capabilities.driveSnapshots, true);
  assert.equal(result.capabilities.driveAffinity, true);
  assert.equal(result.capabilities.timestamps, true);
});

test('memory detail only reads an id already present in pulse metadata', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
  ombre.memoryMap = async () => ({
    stars: [{ id: 'a35f6a3aeb35', title: '兔子小姐和熊先生', domains: ['恋爱'], tags: ['共同记忆'] }],
  });
  ombre.listTools = async () => [{ name: 'breath_search' }];
  let called = null;
  ombre.call = async (name, args) => {
    called = { name, args };
    return {
      result: {
        content: [{
          type: 'text',
          text: '[exact_bucket_id:true] [bucket_id:a35f6a3aeb35]\n我们一起看见了月亮。\n第二行。\n👣 Footprint：最近活跃',
        }],
      },
    };
  };

  const detail = await ombre.memoryDetail('a35f6a3aeb35');

  assert.deepEqual(called, {
    name: 'breath_search',
    args: { query: 'a35f6a3aeb35', max_results: 1 },
  });
  assert.equal(detail.available, true);
  assert.equal(detail.preview, '我们一起看见了月亮。\n第二行。');
  assert.equal(detail.star.title, '兔子小姐和熊先生');
});

test('feel detail uses its dedicated Ombre channel and selects the exact bucket', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
  ombre.memoryMap = async () => ({
    stars: [{ id: 'feel_202608181030_V085', title: '一阵心潮', bucketType: 'feel' }],
  });
  ombre.listTools = async () => [{ name: 'breath' }, { name: 'breath_advanced' }];
  let called = null;
  ombre.call = async (name, args) => {
    called = { name, args };
    return {
      result: {
        content: [{
          type: 'text',
          text: [
            '=== 你留下的 feel（新→旧）===',
            '[2026-08-18] [bucket_id:newer_feel]',
            '另一条感受。',
            '---',
            '[2026-08-18] [bucket_id:feel_202608181030_V085]',
            '晚风吹过来时，我忽然很安心。',
            '👣 Footprint：刚刚想起',
          ].join('\n'),
        }],
      },
    };
  };

  const detail = await ombre.memoryDetail('feel_202608181030_V085');

  assert.deepEqual(called, {
    name: 'breath_advanced',
    args: {
      query: 'feel_202608181030_V085',
      domain: 'feel',
      max_results: 1,
      max_tokens: 6000,
    },
  });
  assert.equal(detail.available, true);
  assert.equal(detail.preview, '晚风吹过来时，我忽然很安心。');
  assert.equal(detail.star.bucketType, 'feel');
});

test('new Ombre feel search retries by title but still verifies the bucket id', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
  ombre.memoryMap = async () => ({
    stars: [{ id: 'feel_202608181030_V085', title: '一阵心潮', bucketType: 'feel' }],
  });
  ombre.listTools = async () => [{ name: 'breath_advanced' }, { name: 'feel' }];
  const calls = [];
  ombre.call = async (name, args) => {
    calls.push({ name, args });
    const text = name === 'feel' && args.query === '一阵心潮'
      ? '[2026-08-18] [bucket_id:feel_202608181030_V085]\n这才是目标感受。'
      : '没有和这个 ID 相关的 feel。';
    return { result: { content: [{ type: 'text', text }] } };
  };

  const detail = await ombre.memoryDetail('feel_202608181030_V085');

  assert.deepEqual(calls, [{
    name: 'feel',
    args: { query: 'feel_202608181030_V085', max_tokens: 6000 },
  }, {
    name: 'feel',
    args: { query: '一阵心潮', max_tokens: 6000 },
  }]);
  assert.equal(detail.available, true);
  assert.equal(detail.preview, '这才是目标感受。');
});

test('transitional Ombre falls back to the legacy feel domain after dedicated search misses', async () => {
  const ombre = new OmbreClient({ readEnabled: true, writeEnabled: false, breathMaxTokens: 800 });
  ombre.memoryMap = async () => ({
    stars: [{ id: 'feel_exact_target', title: '没有字面重合的标题', bucketType: 'feel' }],
  });
  ombre.listTools = async () => [{ name: 'feel' }, { name: 'breath_advanced' }];
  const calls = [];
  ombre.call = async (name, args) => {
    calls.push({ name, args });
    const text = name === 'breath_advanced'
      ? '[2026-08-18] [bucket_id:feel_exact_target]\n旧通道里的目标正文。'
      : '[2026-08-18] [bucket_id:a_similar_feel]\n只是相似，不是目标。';
    return { result: { content: [{ type: 'text', text }] } };
  };

  const detail = await ombre.memoryDetail('feel_exact_target');

  assert.deepEqual(calls.map(({ name }) => name), ['feel', 'feel', 'breath_advanced']);
  assert.equal(detail.available, true);
  assert.equal(detail.preview, '旧通道里的目标正文。');
});

test('memory detail rejects unknown ids without turning Dashboard into a search proxy', async () => {
  const ombre = new OmbreClient({ readEnabled: true, writeEnabled: false });
  ombre.memoryMap = async () => ({ stars: [{ id: 'known-memory', title: 'known' }] });
  let calls = 0;
  ombre.call = async () => { calls += 1; };

  assert.deepEqual(await ombre.memoryDetail('../secret'), { available: false, reason: 'invalid_id' });
  assert.deepEqual(await ombre.memoryDetail('missing-memory'), { available: false, reason: 'not_found' });
  assert.equal(calls, 0);
});

test('memory preview removes exact-read wrappers and stops before another bucket', () => {
  assert.equal(memoryPreview(`
[检索降级]
[exact_bucket_id:true] [bucket_id:first-memory]
真正正文
---
[bucket_id:another-memory]
另一条正文
`, 'first-memory'), '真正正文');
});
