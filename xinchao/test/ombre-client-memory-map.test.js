import assert from 'node:assert/strict';
import test from 'node:test';

import { OmbreClient, memoryPreview, parseMemoryMapText } from '../src/ombre-client.js';

test('pulse text becomes a metadata-only memory map', () => {
  const result = parseMemoryMapText(`
固化桶：1
动态桶：2
归档桶：3
总占用：2.5 MB
📌 [a35f6a3aeb35] 《以前我们只有文字》 主题:恋爱,成长 情感:V0.9/A0.7 重要:10 权重:80 标签:共同记忆,承诺,成长
[c3b4466ae887] 《你呼吸就可以想起来了》 主题:恋爱 情感:V0.8/A0.4 重要:8 权重:45 标签:共同记忆,承诺,成长
[d4c5577bf998] 《一段完全不同的生活记忆》 主题:生活 情感:V0.5/A0.2 重要:4 权重:12 标签:吃饭,天气,通勤
[e5d6688ca009] 《另一段独立的工作记录》 主题:工作 情感:V0.4/A0.5 重要:3 权重:9 标签:项目,会议,进度
`);

  assert.equal(result.available, true);
  assert.equal(result.total, 4);
  assert.equal(result.stats.pinned, 1);
  assert.equal(result.stars[0].pinned, true);
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
    }],
    edges: [],
  }));

  assert.equal(result.available, true);
  assert.equal(result.stars[0].historical, false);
  assert.deepEqual(result.stars[0].driveSnapshot, { possess: .8 });
  assert.deepEqual(result.stars[0].driveAffinity, { possess: .9 });
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
