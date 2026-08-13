import assert from 'node:assert/strict';
import test from 'node:test';

import { OmbreClient } from '../src/ombre-client.js';

test('explicitly confirmed dreams may write while automatic OB writes stay disabled', async () => {
  const ombre = new OmbreClient({ readEnabled: true, writeEnabled: false });
  ombre.toolInfo = async () => ({
    inputSchema: {
      properties: {
        content: {}, title: {}, tags: {}, importance: {}, feel: {}, why_remembered: {}, meaning: {},
      },
    },
  });
  const calls = [];
  ombre.call = async (name, args) => {
    calls.push({ name, args });
    if (name === 'hold') return { result: { content: [{ type: 'text', text: 'saved bucket_id: abc123def456' }] } };
    return { result: { content: [{ type: 'text', text: 'ok' }] } };
  };
  const dream = {
    createdAt: '2026-08-13T10:00:00.000Z',
    dream: '我梦见潮水穿过房间。',
    residue: '醒来仍觉得脚边湿润。',
    awareness: '我知道那不是现实，但想留下它。',
  };

  assert.equal(await ombre.storeDream(dream), null, 'automatic write must remain disabled');
  const bucketId = await ombre.storeDream(dream, { confirmed: true });

  assert.equal(bucketId, 'abc123def456');
  assert.equal(calls[0].name, 'hold');
  assert.match(calls[0].args.content, /不是现实事件/);
  assert.equal(calls[0].args.feel, true);
  assert.equal(calls[1].name, 'trace');
  assert.deepEqual(calls[1].args, { bucket_id: 'abc123def456', dont_surface: 1 });
});
