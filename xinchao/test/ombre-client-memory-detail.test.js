import assert from 'node:assert/strict';
import test from 'node:test';

import { OmbreClient } from '../src/ombre-client.js';

test('memory detail refreshes stale pulse metadata after an exact-read miss', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxTokens: 800,
  });
  let metadataCalls = 0;
  let detailCalls = 0;
  ombre.memoryMetadata = async ({ refresh = false } = {}) => {
    metadataCalls += 1;
    if (!refresh) return new Map([['memory-1', { id: 'memory-1', bucketType: 'dynamic' }]]);
    return new Map([['memory-1', { id: 'memory-1', bucketType: 'feel' }]]);
  };
  ombre.callBreath = async () => {
    detailCalls += 1;
    return { result: { content: [{ type: 'text', text: '没有命中' }] } };
  };
  ombre.callFeelDetail = async () => ({
    result: { content: [{ type: 'text', text: '[bucket_id:memory-1]\n最新的感受正文' }] },
  });

  const result = await ombre.memoryDetail('memory-1');

  assert.equal(result.available, true);
  assert.equal(result.star.bucketType, 'feel');
  assert.equal(detailCalls, 1);
  assert.equal(metadataCalls, 2);
});

test('memory detail does not refresh pulse after the first exact read succeeds', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxTokens: 800,
  });
  let metadataCalls = 0;
  let detailCalls = 0;
  ombre.memoryMetadata = async () => {
    metadataCalls += 1;
    return new Map([['memory-1', { id: 'memory-1', bucketType: 'dynamic' }]]);
  };
  ombre.callBreath = async () => {
    detailCalls += 1;
    return { result: { content: [{ type: 'text', text: '[bucket_id:memory-1]\n仍然存在的正文' }] } };
  };

  const result = await ombre.memoryDetail('memory-1');

  assert.equal(result.available, true);
  assert.equal(metadataCalls, 1);
  assert.equal(detailCalls, 1);
});

test('memory detail stops after one metadata refresh when the exact read still misses', async () => {
  const ombre = new OmbreClient({
    readEnabled: true,
    writeEnabled: false,
    breathMaxTokens: 800,
  });
  let metadataCalls = 0;
  let detailCalls = 0;
  ombre.memoryMetadata = async () => {
    metadataCalls += 1;
    return new Map([['memory-1', { id: 'memory-1', bucketType: 'dynamic' }]]);
  };
  ombre.callBreath = async () => {
    detailCalls += 1;
    return { result: { content: [{ type: 'text', text: '依然没有命中' }] } };
  };

  const result = await ombre.memoryDetail('memory-1');

  assert.deepEqual(result, { available: false, reason: 'not_found' });
  assert.equal(metadataCalls, 2);
  assert.equal(detailCalls, 2);
});
