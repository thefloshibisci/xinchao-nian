#!/usr/bin/env node

// Read-only vNext boundary checks. This file intentionally has no deployment,
// volume, import, or MCP write operation.

const DEFAULT_TIMEOUT_MS = 10000;

function usage() {
  console.log([
    'Usage: node tools/vnext_preflight.mjs --xinchao <url> [--ob <url>]',
    '',
    'Optional private environment variables:',
    '  VNEXT_XINCHAO_TOKEN  Bearer token for the Xinchao MCP endpoint',
    '  VNEXT_OB_TOKEN       Bearer token for the OB MCP endpoint',
  ].join('\n'));
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--help' || arg === '-h') {
      usage();
      process.exit(0);
    }
    if (!arg.startsWith('--')) throw new Error(`Unexpected argument: ${arg}`);
    const key = arg.slice(2);
    const value = argv[index + 1];
    if (!value || value.startsWith('--')) throw new Error(`Missing value for --${key}`);
    result[key] = value;
    index += 1;
  }
  if (!result.xinchao && !result.ob) throw new Error('Provide at least --xinchao or --ob');
  return result;
}

function normalizeBase(value) {
  return String(value).replace(/\/+$/, '');
}

async function request(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const body = await response.text();
    return { response, body };
  } finally {
    clearTimeout(timer);
  }
}

function parseMcpBody(body) {
  const line = String(body).split(/\r?\n/).find((item) => item.startsWith('data:'));
  const raw = line ? line.slice(5).trim() : String(body).trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function printCheck(label, ok, detail) {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${label}${detail ? ` - ${detail}` : ''}`);
  return ok;
}

async function checkHttp(label, url, expectedStatuses) {
  try {
    const { response } = await request(url, { headers: { Accept: 'application/json' } });
    return printCheck(label, expectedStatuses.includes(response.status), `HTTP ${response.status}`);
  } catch (error) {
    return printCheck(label, false, error.name === 'AbortError' ? 'timeout' : error.message);
  }
}

async function checkMcp(label, baseUrl, token) {
  const endpoint = `${normalizeBase(baseUrl)}/mcp`;
  const headers = {
    Accept: 'application/json, text/event-stream',
    'Content-Type': 'application/json',
    'X-VNext-Preflight': 'read-only',
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  try {
    const initialize = await request(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: {
          protocolVersion: '2025-06-18',
          capabilities: {},
          clientInfo: { name: 'vnext-preflight', version: '1.0.0' },
        },
      }),
    });
    if (!printCheck(`${label} initialize`, initialize.response.ok, `HTTP ${initialize.response.status}`)) return false;
    const initializedHeaders = { ...headers };
    const session = initialize.response.headers.get('mcp-session-id');
    if (session) initializedHeaders['Mcp-Session-Id'] = session;

    const listed = await request(endpoint, {
      method: 'POST',
      headers: initializedHeaders,
      body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }),
    });
    const payload = parseMcpBody(listed.body);
    const tools = payload?.result?.tools ?? payload?.tools;
    const ok = listed.response.ok && Array.isArray(tools);
    return printCheck(
      `${label} tools/list`,
      ok,
      ok ? `${tools.length} tools` : `HTTP ${listed.response.status}`,
    );
  } catch (error) {
    return printCheck(`${label} MCP`, false, error.name === 'AbortError' ? 'timeout' : error.message);
  }
}

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`ERROR ${error.message}`);
    usage();
    process.exitCode = 2;
    return;
  }

  let failed = false;
  const record = (ok) => {
    if (!ok) failed = true;
  };
  if (args.xinchao) {
    const base = normalizeBase(args.xinchao);
    record(await checkHttp('xinchao health', `${base}/health`, [200]));
    record(await checkHttp('xinchao MCP boundary', `${base}/mcp`, [200, 401, 403, 404, 405]));
    if (process.env.VNEXT_XINCHAO_TOKEN) {
      record(await checkMcp('xinchao', base, process.env.VNEXT_XINCHAO_TOKEN));
    } else {
      console.log('SKIP xinchao MCP initialize/tools/list - VNEXT_XINCHAO_TOKEN is not set');
    }
  }
  if (args.ob) {
    const base = normalizeBase(args.ob);
    record(await checkHttp('OB health', `${base}/health`, [200]));
    record(await checkHttp('OB version', `${base}/api/version`, [200, 401, 403]));
    record(await checkHttp('OB MCP boundary', `${base}/mcp`, [200, 401, 403, 404, 405]));
    if (process.env.VNEXT_OB_TOKEN) {
      record(await checkMcp('OB', base, process.env.VNEXT_OB_TOKEN));
    } else {
      console.log('SKIP OB MCP initialize/tools/list - VNEXT_OB_TOKEN is not set');
    }
  }

  if (failed) {
    console.error('vNext preflight failed. No deployment or data mutation was attempted.');
    process.exitCode = 1;
  } else {
    console.log('vNext preflight passed. This is a read-only boundary check, not acceptance.');
  }
}

await main();
