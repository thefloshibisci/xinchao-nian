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
