import assert from 'node:assert/strict';
import test from 'node:test';

import { OmbreClient } from '../src/ombre-client.js';

function client() {
  return new OmbreClient({
    readEnabled: true,
    writeEnabled: true,
    breathMaxResults: 3,
    breathMaxTokens: 800,
  });
}

test('new Ombre releases use breath_advanced for bounded semantic recall', async () => {
  const ombre = client();
  ombre.listTools = async () => [{ name: 'breath' }, { name: 'breath_search' }, { name: 'breath_advanced' }];
  let called;
  ombre.call = async (name, args) => {
    called = { name, args };
    return { result: { content: [{ type: 'text', text: 'ok' }] } };
  };

  await ombre.callBreath({ query: '最近的共同经历', maxResults: 3, maxTokens: 800 });
  assert.deepEqual(called, {
    name: 'breath_advanced',
    args: { query: '最近的共同经历', max_results: 3, max_tokens: 800 },
  });
});

test('split Ombre releases without advanced recall fall back to breath_search', async () => {
  const ombre = client();
  ombre.listTools = async () => [{ name: 'breath' }, { name: 'breath_search' }];
  let called;
  ombre.call = async (name, args) => {
    called = { name, args };
    return { result: { content: [{ type: 'text', text: 'ok' }] } };
  };

  await ombre.callBreath({ query: '牵挂', maxResults: 2, maxTokens: 500 });
  assert.deepEqual(called, {
    name: 'breath_search',
    args: { query: '牵挂', max_results: 2 },
  });
});

test('legacy Ombre releases keep the parameterised breath call', async () => {
  const ombre = client();
  ombre.listTools = async () => [{ name: 'breath' }];
  let called;
  ombre.call = async (name, args) => {
    called = { name, args };
    return { result: { content: [{ type: 'text', text: 'ok' }] } };
  };

  await ombre.callBreath({ query: '牵挂', maxResults: 2, maxTokens: 500 });
  assert.deepEqual(called, {
    name: 'breath',
    args: { query: '牵挂', max_results: 2, max_tokens: 500 },
  });
});

test('dream writes omit legacy fields unsupported by current Ombre hold', async () => {
  const ombre = client();
  ombre.listTools = async () => [{
    name: 'hold',
    inputSchema: {
      type: 'object',
      properties: {
        content: { type: 'string' },
        tags: { type: 'string' },
        importance: { type: 'integer' },
      },
    },
  }];
  let called;
  ombre.call = async (name, args) => {
    called = { name, args };
    return { result: { content: [{ type: 'text', text: 'stored' }] } };
  };

  await ombre.storeDream({ dream: '海', residue: '潮声', awareness: '醒来' });
  assert.equal(called.name, 'hold');
  assert.deepEqual(Object.keys(called.args).sort(), ['content', 'importance', 'tags']);
  assert.equal(called.args.tags, '梦境,心潮,非现实');
  assert.equal(called.args.importance, 6);
});
