import assert from 'node:assert/strict';
import test from 'node:test';

import { OmbreClient } from '../src/ombre-client.js';

test('session refresh invalidates the cached Ombre tool list', async () => {
  const ombre = new OmbreClient({
    url: 'http://ombre.invalid/mcp',
    token: '',
    readEnabled: true,
    writeEnabled: true,
  });
  ombre.toolsCache = [{ name: 'stale_tool' }];
  ombre.memoryMetadataCache = { map: new Map([['old', {}]]), expiresAt: Date.now() + 60_000 };
  ombre.sessionId = 'old-session';
  ombre._initialized = true;

  ombre.resetSessionState();

  assert.equal(ombre.sessionId, null);
  assert.equal(ombre._initialized, false);
  assert.equal(ombre.toolsCache, null);
  assert.equal(ombre.memoryMetadataCache, null);
});

test('an expired Ombre session reloads tools from the replacement session', async () => {
  const ombre = new OmbreClient({
    url: 'http://ombre.invalid/mcp',
    token: '',
    readEnabled: true,
    writeEnabled: true,
  });
  ombre.toolsCache = [{ name: 'stale_tool' }];
  ombre.sessionId = 'old-session';
  ombre._initialized = true;

  let toolCallAttempts = 0;
  let toolListCalls = 0;
  ombre.post = async (payload) => {
    if (payload.method === 'tools/call') {
      toolCallAttempts += 1;
      if (toolCallAttempts === 1) throw new Error('Ombre MCP failed: HTTP 404');
      return { result: { content: [{ type: 'text', text: 'ok' }] } };
    }
    if (payload.method === 'initialize') {
      ombre.sessionId = 'new-session';
      return { result: {} };
    }
    if (payload.method === 'tools/list') {
      toolListCalls += 1;
      return { result: { tools: [{ name: 'fresh_tool' }] } };
    }
    return null;
  };

  await ombre.call('stale_tool', {});
  const tools = await ombre.listTools();

  assert.equal(toolCallAttempts, 2);
  assert.equal(toolListCalls, 1);
  assert.equal(ombre.sessionId, 'new-session');
  assert.deepEqual(tools, [{ name: 'fresh_tool' }]);
});

test('keeps the last known OB tool schema during a transient outage', async () => {
  const ombre = new OmbreClient({
    url: 'http://ombre.invalid/mcp',
    token: '',
    readEnabled: true,
    writeEnabled: true,
    toolsListRetryDelayMs: 0,
  });

  let calls = 0;
  ombre.post = async (payload) => {
    if (payload.method === 'initialize') {
      ombre.sessionId = `session-${calls}`;
      return { result: {} };
    }
    if (payload.method === 'tools/list') {
      calls += 1;
      if (calls === 1) return { result: { tools: [{ name: 'breath' }] } };
      throw new Error('fetch failed: socket closed');
    }
    return null;
  };

  const first = await ombre.listTools();
  const duringRestart = await ombre.listTools({ refresh: true });

  assert.deepEqual(first, [{ name: 'breath' }]);
  assert.deepEqual(duringRestart, [{ name: 'breath' }]);
  assert.equal(calls, 4, 'the failed refresh retries twice before using the stale schema');
  assert.equal(ombre.toolsCache, null, 'stale fallback must not masquerade as a healthy current-session cache');
});
