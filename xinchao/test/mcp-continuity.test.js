import test from 'node:test';
import assert from 'node:assert/strict';
import { handleMcpMessage } from '../src/mcp-protocol.js';

function message(method, params = {}) {
  return { jsonrpc: '2.0', id: 1, method, params };
}

test('hides continuity sync when the feature is disabled', async () => {
  const result = await handleMcpMessage(message('tools/list'), {});
  assert.equal(result.status, 200);
  assert.equal(result.body.result.tools.some((tool) => tool.name === 'xinchao_continuity_sync'), false);
});

test('exposes and validates continuity sync when enabled', async () => {
  let received;
  const handlers = {
    defaultSessionId: 'mcp-window',
    continuitySync: async (args) => {
      received = args;
      return { accepted: 1, duplicates: 0, text: 'other window context' };
    },
  };
  const listed = await handleMcpMessage(message('tools/list'), handlers);
  assert.equal(listed.body.result.tools.some((tool) => tool.name === 'xinchao_continuity_sync'), true);

  const called = await handleMcpMessage(message('tools/call', {
    name: 'xinchao_continuity_sync',
    arguments: {
      client: 'rikkahub',
      messages: [{ turn_id: 'turn-1', role: 'user', text: '刚刚聊到这里' }],
      limit: 6,
    },
  }), handlers);
  assert.equal(called.status, 200);
  assert.equal(called.body.result.isError, false);
  assert.deepEqual(received, {
    sessionId: 'mcp-window',
    client: 'rikkahub',
    messages: [{ turnId: 'turn-1', role: 'user', text: '刚刚聊到这里' }],
    limit: 6,
  });
});

test('keeps existing server-path media unchanged while proxying hold to OB', async () => {
  let received;
  const handlers = {
    callOb: async (name, args) => {
      received = { name, args };
      return { result: { content: [{ type: 'text', text: 'ok' }] } };
    },
  };

  // A server-readable path remains untouched here; URL conversion itself is
  // covered independently without making this protocol test use the network.
  const called = await handleMcpMessage(message('tools/call', {
    name: 'hold',
    arguments: { content: 'memory', media: '/srv/photo.png' },
  }), handlers);
  assert.equal(called.body.result.isError, undefined);
  assert.deepEqual(received, {
    name: 'hold',
    args: { content: 'memory', media: '/srv/photo.png' },
  });
});
