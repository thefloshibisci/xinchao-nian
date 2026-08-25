import test from 'node:test';
import assert from 'node:assert/strict';
import { OB_PROXY_TOOLS, handleMcpMessage } from '../src/mcp-protocol.js';

function message(method, params = {}) {
  return { jsonrpc: '2.0', id: 1, method, params };
}

const addedTools = [
  'breath_search',
  'breath_advanced',
  'plan',
  'letter_write',
  'letter_read',
];

test('exposes reviewed recall, plan and letter tools from OB', async () => {
  const obTools = addedTools.map((name) => ({
    name,
    description: `raw ${name}`,
    inputSchema: { type: 'object', properties: {} },
  }));
  const result = await handleMcpMessage(message('tools/list'), {
    listObTools: async () => obTools,
  });

  const listed = new Map(result.body.result.tools.map((tool) => [tool.name, tool]));
  for (const name of addedTools) {
    assert.equal(OB_PROXY_TOOLS.includes(name), true);
    assert.equal(listed.has(name), true);
    assert.notEqual(listed.get(name).description, `raw ${name}`);
  }
  assert.equal(OB_PROXY_TOOLS.includes('source_read'), false);
});

test('routes newly exposed OB tools through the same gateway', async () => {
  const calls = [];
  const handlers = {
    callOb: async (name, args) => {
      calls.push({ name, args });
      return { result: { content: [{ type: 'text', text: 'ok' }] } };
    },
  };

  for (const name of addedTools) {
    const result = await handleMcpMessage(message('tools/call', {
      name,
      arguments: { marker: name },
    }), handlers);
    assert.equal(result.status, 200);
    assert.equal(result.body.result.isError, undefined);
  }
  assert.deepEqual(calls, addedTools.map((name) => ({ name, args: { marker: name } })));
});
