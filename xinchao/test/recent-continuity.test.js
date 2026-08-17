import test from 'node:test';
import assert from 'node:assert/strict';
import {
  newContinuityState,
  recordRecentTurn,
  recentTurns,
  RECENT_CONTINUITY_LIMITS,
} from '../src/recent-continuity.js';

const now = new Date('2026-08-18T12:00:00.000Z');

function add(state, overrides = {}) {
  return recordRecentTurn(state, {
    profileId: 'shared',
    sessionId: 'rikkahub',
    turnId: 'turn-1',
    role: 'user',
    text: '刚刚聊到跨端连续性',
    source: 'rikkahub',
    now,
    ...overrides,
  });
}

test('shares recent turns across sessions in one profile', () => {
  const first = add(newContinuityState()).state;
  const second = add(first, {
    sessionId: 'codex', turnId: 'turn-2', role: 'assistant', text: '我接上了。', source: 'codex',
    now: new Date(now.getTime() + 1000),
  }).state;
  assert.deepEqual(recentTurns(second, { profileId: 'shared', sessionId: 'new-window' })
    .map((item) => item.text), ['刚刚聊到跨端连续性', '我接上了。']);
});

test('keeps profiles isolated', () => {
  const state = add(newContinuityState()).state;
  assert.equal(recentTurns(state, { profileId: 'someone-else' }).length, 0);
});

test('deduplicates retries by profile, session, turn and role', () => {
  const first = add(newContinuityState());
  const retry = add(first.state);
  assert.equal(retry.duplicate, true);
  assert.equal(recentTurns(retry.state, { profileId: 'shared' }).length, 1);
});

test('expires items and bounds retained count', () => {
  let state = newContinuityState();
  for (let index = 0; index < RECENT_CONTINUITY_LIMITS.maxItems + 5; index += 1) {
    state = add(state, {
      turnId: `turn-${index}`,
      text: `message-${index}`,
      now: new Date(now.getTime() + index * 1000),
      ttlHours: 1,
    }).state;
  }
  assert.equal(recentTurns(state, {
    profileId: 'shared', limit: RECENT_CONTINUITY_LIMITS.maxItems,
    now: new Date(now.getTime() + 30_000),
  }).length,
    RECENT_CONTINUITY_LIMITS.maxItems);
  assert.equal(recentTurns(state, { profileId: 'shared', now: new Date(now.getTime() + 3_700_000) }).length, 0);
});

test('can restrict reads to one session', () => {
  const first = add(newContinuityState()).state;
  const second = add(first, {
    sessionId: 'codex', turnId: 'turn-2', text: 'desktop only',
    now: new Date(now.getTime() + 1000),
  }).state;
  assert.deepEqual(recentTurns(second, {
    profileId: 'shared', sessionId: 'codex', includeAllSessions: false,
  }).map((item) => item.text), ['desktop only']);
});
