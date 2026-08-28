import test from 'node:test';
import assert from 'node:assert/strict';
import { parseBearerAuthorization, validateGatewayContinuityRequest } from '../src/http-contract.js';

const baseRequest = {
  session_id: 'gateway:desktop:main',
  client: 'gateway-desktop',
  messages: [
    { turn_id: 'round-1:user', role: 'user', text: '刚刚聊到这里' },
    { turn_id: 'round-1:assistant', role: 'assistant', text: '我接上了' },
  ],
  limit: 6,
};

test('parses only bearer authorization headers', () => {
  assert.equal(parseBearerAuthorization({ headers: { authorization: 'Bearer token' } }), 'token');
  assert.equal(parseBearerAuthorization({ headers: { authorization: 'bearer token' } }), 'token');
  assert.equal(parseBearerAuthorization({ headers: {} }), null);
  assert.equal(parseBearerAuthorization({ headers: { authorization: 'Basic token' } }), null);
});

test('normalizes gateway session labels deterministically', () => {
  assert.equal(validateGatewayContinuityRequest({
    ...baseRequest,
    session_id: ' gateway: desktop : main ',
  }).sessionId, 'gateway:desktop:main');
  assert.equal(validateGatewayContinuityRequest({
    ...baseRequest,
    session_id: 'gateway:desktop:bad/space',
  }).sessionId, 'gateway:desktop:bad-space');
  const longLabel = 'gateway:desktop:' + 'a'.repeat(200);
  const normalized = validateGatewayContinuityRequest({
    ...baseRequest,
    session_id: longLabel,
  }).sessionId;
  assert.match(normalized, /^gateway:desktop:a+:([0-9a-f]{24})$/);
  assert.equal(normalized.length, 160);
});

test('accepts only conversation roles for gateway HTTP continuity', () => {
  assert.deepEqual(validateGatewayContinuityRequest(baseRequest).messages.map((message) => message.role), [
    'user',
    'assistant',
  ]);
  for (const role of ['system', 'tool', 'function', 'developer']) {
    assert.throws(() => validateGatewayContinuityRequest({
      ...baseRequest,
      messages: [{ ...baseRequest.messages[0], role }],
    }), /role 只能是 user 或 assistant/);
  }
});

test('rejects credential-like and injected context blocks', () => {
  assert.throws(() => validateGatewayContinuityRequest({
    ...baseRequest,
    messages: [{ ...baseRequest.messages[0], text: 'SERVICE_TOKEN=abc' }],
  }), /包含凭据或注入块/);
  for (const heading of ['Xinchao Recent Context', 'Recalled Memory', 'Core Memory']) {
    assert.throws(() => validateGatewayContinuityRequest({
      ...baseRequest,
      messages: [{ ...baseRequest.messages[0], text: `${heading}: secret` }],
    }), /包含凭据或注入块/);
  }
  assert.equal(validateGatewayContinuityRequest({
    ...baseRequest,
    messages: [{ ...baseRequest.messages[0], text: 'Recent context is normal words' }],
  }).messages[0].text, 'Recent context is normal words');
});

test('keeps bounded normalization and returns a synchronizable payload', () => {
  const normalized = validateGatewayContinuityRequest({
    ...baseRequest,
    messages: [
      { turn_id: `turn-${'x'.repeat(180)}`, role: ' assistant ', text: `  text\n${'a'.repeat(2200)}  ` },
      { turn_id: 'same-turn', role: 'user', text: 'again' },
    ],
  });
  assert.equal(normalized.messages[0].turnId.length, 160);
  assert.equal(normalized.messages[0].text.length, 2000);
  assert.equal(normalized.limit, 6);
  assert.deepEqual(Object.keys(normalized), ['sessionId', 'client', 'messages', 'limit']);
});

test('rejects structural errors before the continuity store is touched', () => {
  assert.throws(() => validateGatewayContinuityRequest({ ...baseRequest, session_id: '' }), /session_id 是必填项/);
  assert.throws(() => validateGatewayContinuityRequest({ ...baseRequest, messages: [] }), /messages 必须是非空数组/);
  assert.throws(() => validateGatewayContinuityRequest({
    ...baseRequest,
    messages: [{ turn_id: 'same-turn', role: 'user', text: 'again' }, { turn_id: 'same-turn', role: 'user', text: 'again' }],
  }), /重复 turn_id/);
});

test('preserves partial success semantics with duplicate turn ids across roles', () => {
  const normalized = validateGatewayContinuityRequest({
    session_id: 'gateway:desktop:main',
    messages: [
      { turn_id: 'round-1:user', role: 'user', text: 'hi' },
      { turn_id: 'round-1:assistant', role: 'assistant', text: 'hello' },
    ],
  });
  assert.equal(normalized.limit, 8);
  assert.deepEqual(normalized.messages.map((message) => message.turnId), [
    'round-1:user',
    'round-1:assistant',
  ]);
});
