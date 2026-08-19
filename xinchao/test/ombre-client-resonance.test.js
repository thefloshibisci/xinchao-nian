import assert from 'node:assert/strict';
import test from 'node:test';

import {
  OmbreClient,
  parseSurfacedBucketIds,
  parseSurfacedDomains,
} from '../src/ombre-client.js';

test('current Ombre bucket ids are parsed from surfaced breath text', () => {
  const text = `
[联想浮现·非检索命中] [bucket_id:6ef1886eb8d3]
一段正文
---
[bucket_id:24fee7f0d130] 另一段正文
[bucket_id:6ef1886eb8d3] 重复不应重复计数
`;
  assert.deepEqual(parseSurfacedBucketIds(text), ['6ef1886eb8d3', '24fee7f0d130']);
});

test('legacy embedded domains remain supported', () => {
  assert.deepEqual(
    parseSurfacedDomains('[domain:恋爱,编程] [domain:恋爱]'),
    ['恋爱', '编程'],
  );
});

test('current Ombre pulse metadata maps surfaced bucket ids back to domains', async () => {
  const ombre = new OmbreClient({ readEnabled: true, writeEnabled: false });
  let pulseCalls = 0;
  ombre.memoryMap = async () => {
    pulseCalls += 1;
    return {
      stars: [
        { id: '6ef1886eb8d3', domains: ['编程', '自省'], tags: ['技术'] },
        { id: '24fee7f0d130', domains: ['恋爱', '自省'], tags: ['生活'] },
      ],
    };
  };

  const first = await ombre.surfacedMetadata(`
[bucket_id:6ef1886eb8d3]
[bucket_id:24fee7f0d130]
`);
  const second = await ombre.surfacedMetadata('[bucket_id:24fee7f0d130]');

  assert.deepEqual(first.bucketIds, ['6ef1886eb8d3', '24fee7f0d130']);
  assert.deepEqual(first.domains, ['编程', '自省', '恋爱']);
  assert.deepEqual(first.tags, ['技术', '生活']);
  assert.deepEqual(second.domains, ['恋爱', '自省']);
  assert.equal(pulseCalls, 1, 'pulse metadata should be cached briefly');
});

test('dream recall rotates its angle and retries once when all first results were recently used', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
  const queries = [];
  ombre.callBreath = async ({ query }) => {
    queries.push(query);
    const id = queries.length === 1 ? 'old-memory' : 'new-memory';
    return { result: { content: [{ type: 'text', text: `[bucket_id:${id}]\n具体记忆 ${id}` }] } };
  };

  const recalled = await ombre.dreamMaterial([], [{
    id: 'previous-dream',
    sourceMemoryIds: ['old-memory'],
  }]);

  assert.equal(queries.length, 2);
  assert.match(queries[0], /最近没说完的话/);
  assert.match(queries[1], /较久没有浮现/);
  assert.deepEqual(recalled.bucketIds, ['new-memory']);
  assert.deepEqual(recalled.skippedBucketIds, []);
  assert.match(recalled.text, /new-memory/);
});

test('old dreams without source ids remain compatible with rotating dream recall', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
  let calls = 0;
  ombre.callBreath = async () => {
    calls += 1;
    return { result: { content: [{ type: 'text', text: '[bucket_id:first-source]\n一段新材料' }] } };
  };

  const recalled = await ombre.dreamMaterial([], [{ id: 'legacy-dream' }]);

  assert.equal(calls, 1);
  assert.deepEqual(recalled.bucketIds, ['first-source']);
});

test('dream recall removes reused buckets even when OB mixes old and new candidates', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
  ombre.callBreath = async () => ({ result: { content: [{ type: 'text', text: [
    '[bucket_id:old-memory]\n旧素材',
    '---',
    '[bucket_id:new-memory]\n新素材',
  ].join('\n') }] } });

  const recalled = await ombre.dreamMaterial([], [{ sourceMemoryIds: ['old-memory'] }]);

  assert.deepEqual(recalled.bucketIds, ['new-memory']);
  assert.doesNotMatch(recalled.text, /old-memory/);
  assert.match(recalled.text, /新素材/);
});

test('dream recall returns no repeated material after every angle only finds used buckets', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
  let calls = 0;
  ombre.callBreath = async () => {
    calls += 1;
    return { result: { content: [{ type: 'text', text: '[bucket_id:old-memory]\n重复素材' }] } };
  };

  const recalled = await ombre.dreamMaterial([], [{ sourceMemoryIds: ['old-memory'] }]);

  assert.equal(calls, 3);
  assert.deepEqual(recalled.bucketIds, []);
  assert.equal(recalled.text, '');
});
