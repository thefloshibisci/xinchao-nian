import assert from 'node:assert/strict';
import test from 'node:test';

import { ModelClient } from '../src/model-client.js';

function client() {
  return new ModelClient({
    enabled: true,
    apiKey: 'test-key',
    baseUrl: 'https://api.siliconflow.cn/v1',
    name: 'Qwen/Qwen3.5-35B-A3B',
    timeoutMs: 30000,
    maxInputChars: 10000,
    maxOutputTokens: 650,
    agentName: '南枳',
    notificationRecipient: '严槿',
  });
}

test('SiliconFlow-compatible requests disable Qwen thinking with enable_thinking', async (t) => {
  const bodies = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    bodies.push(JSON.parse(options.body));
    return new Response(JSON.stringify({ choices: [{ message: { content: '{"dream":"海","residue":"潮声","awareness":"我醒了","lucidity":0.2}' } }] }), { status: 200 });
  });
  const result = await client().generateDream({ state: { consciousness: 'sleeping' }, material: '', topDrives: [] });
  assert.equal(result.source, 'model');
  assert.equal(bodies[0].enable_thinking, false);
  assert.equal('thinking' in bodies[0], false);
  assert.deepEqual(bodies[0].response_format, { type: 'json_object' });
});

test('optional JSON and vendor parameters degrade safely on 400/422', async (t) => {
  const bodies = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    const body = JSON.parse(options.body);
    bodies.push(body);
    if (bodies.length === 1) return new Response('json mode unsupported', { status: 400 });
    if (bodies.length === 2) return new Response('enable_thinking unsupported', { status: 422 });
    return new Response(JSON.stringify({ choices: [{ message: { content: '{"message":"忽然想起你了。"}' } }] }), { status: 200 });
  });
  const result = await client().generateThought({ state: { consciousness: 'awake' }, topDrives: [], material: '' });
  assert.equal(result.message, '忽然想起你了。');
  assert.ok(bodies[0].response_format);
  assert.equal(bodies[0].enable_thinking, false);
  assert.equal('response_format' in bodies[1], false);
  assert.equal(bodies[1].enable_thinking, false);
  assert.equal('enable_thinking' in bodies[2], false);
});
