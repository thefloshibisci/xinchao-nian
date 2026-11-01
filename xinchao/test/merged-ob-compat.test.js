import assert from 'node:assert/strict';
import test from 'node:test';
import { OmbreClient } from '../src/ombre-client.js';

test('recent and continuity reads use the bounded API on current OB', async () => {
  const client = new OmbreClient({ breathMaxResults: 3, breathMaxTokens: 800 });
  client.listTools = async () => [{ name: 'breath' }, { name: 'breath_advanced' }];
  const calls = [];
  client.call = async (name, args) => {
    calls.push({ name, args });
    return { result: { content: [{ type: 'text', text: '[bucket_id:abcdef123456] a memory' }] } };
  };
  await client.recentMaterialWithRefs([], { valence: 0.6, arousal: 0.3 });
  await client.recentContinuityMaterial(1000);
  assert.deepEqual(calls.map(x => x.name), ['breath_advanced', 'breath_advanced']);
  assert.equal(calls[0].args.valence, 0.6);
  assert.equal(calls[1].args.max_tokens, 1000);
});

test('a building memory map is not cached as missing metadata', async () => {
  const client = new OmbreClient({ readEnabled: true });
  let calls = 0;
  client.memoryMap = async () => ++calls === 1
    ? { available: false, stars: [] }
    : { available: true, stars: [{ id: 'abcdef123456', domains: ['生活'] }] };
  assert.deepEqual((await client.surfacedMetadata('[bucket_id:abcdef123456]')).domains, []);
  assert.deepEqual((await client.surfacedMetadata('[bucket_id:abcdef123456]')).domains, ['生活']);
});

test('preview fallback respects AI-locked letters and bounds ordinary content', async (t) => {
  const client = new OmbreClient({ url: 'http://ob.test/mcp', readEnabled: true });
  let locked = true;
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url.pathname.includes('bucket-preview')) return new Response('{}', { status: 404 });
    assert.equal(url.pathname, '/api/bucket/abcdef123456');
    return Response.json({ id: 'abcdef123456', letter_locked: locked, content: 'first\nsecond\nthird' });
  });
  assert.equal((await client.memoryBucketPreview('abcdef123456')).available, false);
  locked = false;
  assert.equal((await client.memoryBucketPreview('abcdef123456', 2)).preview, 'first\nsecond');
});
