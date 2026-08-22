#!/usr/bin/env node

// Read-only validation for isolated vNext environment variables.
// This script never contacts Zeabur, starts a service, or calls MCP.

const ROLE = process.argv[2];
const allowedRoles = new Set(['ob', 'xinchao']);
const forbiddenProductionHosts = new Set([
  'xinchao-nanzhi.zeabur.app',
  'ombre-brain.zeabur.app',
]);

function fail(message) {
  console.error(`FAIL ${message}`);
  process.exitCode = 1;
}

function requireValue(name, { minLength = 1 } = {}) {
  const value = String(process.env[name] ?? '').trim();
  if (!value) {
    fail(`${name} is missing`);
    return '';
  }
  if (/^replace-with/i.test(value) || /^<[^>]+>$/.test(value)) {
    fail(`${name} still contains a template placeholder`);
  }
  if (value.length < minLength) fail(`${name} must be at least ${minLength} characters`);
  return value;
}

function bool(name, expected) {
  const actual = String(process.env[name] ?? '').trim().toLowerCase();
  if (actual !== expected) fail(`${name} must be ${expected}`);
}

function url(name, { protocol, path = '' } = {}) {
  const value = requireValue(name);
  if (!value) return null;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    fail(`${name} must be a valid URL`);
    return null;
  }
  if (protocol && parsed.protocol !== protocol) fail(`${name} must use ${protocol}`);
  if (path && parsed.pathname !== path) fail(`${name} must end at ${path}`);
  if (forbiddenProductionHosts.has(parsed.hostname)) {
    fail(`${name} points at a production host`);
  }
  return parsed;
}

if (!allowedRoles.has(ROLE)) {
  console.error('Usage: node tools/vnext_config_check.mjs <ob|xinchao>');
  process.exitCode = 2;
} else if (ROLE === 'ob') {
  requireValue('OMBRE_MCP_SERVICE_TOKEN', { minLength: 32 });
  requireValue('OMBRE_DASHBOARD_PASSWORD', { minLength: 16 });
  requireValue('DYNAMIC_MIND_TOKEN', { minLength: 32 });
  const dynamicMind = url('DYNAMIC_MIND_URL', { protocol: 'http:' });
  if (dynamicMind && dynamicMind.port && dynamicMind.port !== '18110') {
    fail('DYNAMIC_MIND_URL must use port 18110');
  }
  bool('OMBRE_MCP_REQUIRE_AUTH', 'true');
  if (process.env.OMBRE_BUCKETS_DIR !== '/app/buckets') fail('OMBRE_BUCKETS_DIR must be /app/buckets');
  if (process.env.OMBRE_CONFIG_PATH !== '/app/buckets/config.yaml') fail('OMBRE_CONFIG_PATH must be /app/buckets/config.yaml');
  console.log(process.exitCode ? 'vNext OB config rejected.' : 'vNext OB config passed.');
} else {
  requireValue('SERVICE_TOKEN', { minLength: 32 });
  requireValue('OMBRE_MCP_TOKEN', { minLength: 32 });
  url('OMBRE_MCP_URL', { protocol: 'http:', path: '/mcp' });
  url('OAUTH_PUBLIC_BASE_URL', { protocol: 'https:' });
  requireValue('OAUTH_APPROVAL_TOKEN', { minLength: 32 });
  bool('OMBRE_READ_ENABLED', 'true');
  bool('OMBRE_WRITE_ENABLED', 'false');
  bool('MCP_ENABLED', 'true');
  bool('OAUTH_ENABLED', 'true');
  if (!String(process.env.STATE_PATH ?? '').startsWith('/app/state/')) fail('STATE_PATH must be on /app/state');
  if (!String(process.env.RECENT_CONTINUITY_STATE_PATH ?? '').startsWith('/app/state/')) fail('RECENT_CONTINUITY_STATE_PATH must be on /app/state');
  console.log(process.exitCode ? 'vNext Xinchao config rejected.' : 'vNext Xinchao config passed.');
}
