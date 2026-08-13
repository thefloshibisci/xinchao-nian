import assert from 'node:assert/strict';
import test from 'node:test';

import { handleMcpMessage, XINCHAO_TOOLS } from '../src/mcp-protocol.js';

const call = (name, args, handlers) => handleMcpMessage({
  jsonrpc: '2.0',
  id: 1,
  method: 'tools/call',
  params: { name, arguments: args },
}, handlers);

test('dream review tools are exposed with an explicit confirmation gate', () => {
  const names = XINCHAO_TOOLS.map((tool) => tool.name);
  assert.ok(names.includes('xinchao_dreams_pending'));
  assert.ok(names.includes('xinchao_dream_confirm'));
  const confirm = XINCHAO_TOOLS.find((tool) => tool.name === 'xinchao_dream_confirm');
  assert.deepEqual(confirm.inputSchema.required, ['dream_id', 'confirm']);
});

test('pending dream tool returns only unsaved dreams', async () => {
  const response = await call('xinchao_dreams_pending', {}, {
    pendingDreams: async () => [{ id: 'dream-1', awareness: '我醒来时还记得潮声。' }],
  });
  assert.equal(response.body.result.isError, false);
  assert.equal(response.body.result.structuredContent.dreams[0].id, 'dream-1');
});

test('dream confirmation refuses missing explicit true', async () => {
  let invoked = false;
  const response = await call('xinchao_dream_confirm', { dream_id: 'dream-1', confirm: false }, {
    confirmDream: async () => { invoked = true; },
  });
  assert.equal(response.body.result.isError, true);
  assert.equal(invoked, false);
});

test('confirmed dream routes through the dedicated handler', async () => {
  const response = await call('xinchao_dream_confirm', { dream_id: 'dream-1', confirm: true }, {
    confirmDream: async (args) => ({
      dreamId: args.dreamId,
      bucketId: 'abc123def456',
      alreadySaved: false,
    }),
  });
  assert.equal(response.body.result.isError, false);
  assert.equal(response.body.result.structuredContent.bucketId, 'abc123def456');
});
