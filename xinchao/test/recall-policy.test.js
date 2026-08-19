import assert from 'node:assert/strict';
import test from 'node:test';

import { classifyRecallIntent, planRecall, withRecallPolicyHint } from '../src/recall-policy.js';

test('ambient automatic recall is capped at one candidate and allows an empty result', () => {
  const policy = planRecall({ purpose: 'thought', maxResults: 8, maxTokens: 1600 });
  assert.equal(policy.intent, 'ambient');
  assert.equal(policy.maxResults, 1);
  assert.equal(policy.maxTokens, 700);
  assert.equal(policy.emptyAllowed, true);
});

test('explicit history requests retain a wider budget', () => {
  assert.equal(classifyRecallIntent('请找出我们之前说过的原句'), 'explicit_detail');
  const policy = planRecall({ query: '请找出原句', purpose: 'detail', maxResults: 10, maxTokens: 5000 });
  assert.equal(policy.maxResults, 5);
  assert.equal(policy.maxTokens, 3000);
});

test('dream and recent continuity use separate bounded budgets', () => {
  assert.equal(planRecall({ purpose: 'dream', maxResults: 8 }).maxResults, 2);
  assert.equal(planRecall({ purpose: 'continuity', maxResults: 8 }).maxResults, 5);
});

test('policy hint makes no-hit behaviour explicit', () => {
  const policy = planRecall({ purpose: 'thought' });
  assert.match(withRecallPolicyHint('一件具体的事', policy), /没有就返回空/);
});

