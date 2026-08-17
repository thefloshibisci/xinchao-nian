import test from 'node:test';
import assert from 'node:assert/strict';
import { newState, dreamAllowed, recordDream } from '../src/engine.js';

const window = { enabled: true, timeZone: 'Asia/Shanghai', startHour: 3, endHour: 5 };
function sleeping() {
  const state = newState(new Date('2026-08-16T16:00:00.000Z'));
  state.consciousness = 'sleeping';
  return state;
}

test('dream window allows the first sleeping settle between 03:00 and 05:00 China time', () => {
  assert.equal(dreamAllowed(sleeping(), new Date('2026-08-16T19:15:00.000Z'), 24, 4, window), true);
});

test('dream window rejects evening and late-morning dreams', () => {
  assert.equal(dreamAllowed(sleeping(), new Date('2026-08-16T10:45:00.000Z'), 24, 4, window), false);
  assert.equal(dreamAllowed(sleeping(), new Date('2026-08-16T21:15:00.000Z'), 24, 4, window), false);
});

test('dream window allows only one dream per local day even when UTC date differs', () => {
  let state = sleeping();
  state = recordDream(state, {
    id: 'night-dream', createdAt: '2026-08-16T19:15:00.000Z', dream: 'x', residue: 'y', awareness: 'z'
  });
  // Both timestamps are Aug 17 in Asia/Shanghai, although recordDream indexes
  // usage under the UTC date Aug 16.
  assert.equal(dreamAllowed(state, new Date('2026-08-16T20:30:00.000Z'), 1, 12, window), false);
});

test('disabled window preserves rolling cooldown behavior', () => {
  const state = sleeping();
  state.recentDreams.push({ createdAt: '2026-08-16T18:45:00.000Z' });
  assert.equal(dreamAllowed(state, new Date('2026-08-16T19:15:00.000Z'), 24, 4, { enabled: false }), false);
});
