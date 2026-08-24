#!/usr/bin/env node

// Offline topology guard for the isolated Ombre Brain vNext deployment.
// This check never contacts Zeabur, starts a service, mutates a volume, or
// reads secret values. It exists to catch the exact failure mode where the
// public vNext URL is accidentally built from the OB Dockerfile instead of
// the standalone Xinchao Dockerfile.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FORBIDDEN_HOSTS = [
  'xinchao-nanzhi.zeabur.app',
  'ombre-brain.zeabur.app',
];

function read(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8');
}

function check(label, condition, detail = '') {
  console.log(`${condition ? 'PASS' : 'FAIL'} ${label}${detail ? ` - ${detail}` : ''}`);
  return condition;
}

function contains(text, value) {
  return text.includes(value);
}

function assertNoProductionHosts(label, text) {
  const found = FORBIDDEN_HOSTS.filter((host) => text.includes(host));
  return check(label, found.length === 0, found.length ? found.join(', ') : 'no production host literals');
}

function main() {
  let ok = true;
  const rootDockerfile = read('Dockerfile');
  const obDockerfile = read('ombre-brain/Dockerfile');
  const compose = read('compose.yaml');
  const obEnv = read('deploy/vnext/ob.env.example');
  const xinchaoEnv = read('deploy/vnext/xinchao.env.example');
  const deployReadme = read('deploy/vnext/README.md');

  ok &&= check('root Dockerfile is the standalone Xinchao image',
    contains(rootDockerfile, 'CMD ["node", "src/server.js"]') &&
    contains(rootDockerfile, 'EXPOSE 18110') &&
    contains(rootDockerfile, 'COPY xinchao/src ./src'));
  ok &&= check('OB Dockerfile remains a separate image',
    contains(obDockerfile, 'ENTRYPOINT ["./entrypoint.sh"]') &&
    contains(obDockerfile, 'EXPOSE 8000'));

  ok &&= check('Compose contains separate OB and Xinchao services',
    contains(compose, '  ombre-brain:') && contains(compose, '  dynamic-mind:'));
  ok &&= check('Compose keeps service-to-service dependency explicit',
    contains(compose, '    depends_on:') && contains(compose, '      - ombre-brain'));
  ok &&= check('Compose uses distinct persistent volumes',
    contains(compose, 'ombre-buckets:/app/buckets') &&
    contains(compose, 'xinchao-state:/app/state') &&
    contains(compose, '  ombre-buckets:') && contains(compose, '  xinchao-state:'));

  ok &&= check('vNext Xinchao template keeps writes disabled',
    /(?:^|\n)OMBRE_READ_ENABLED=true(?:\n|$)/.test(xinchaoEnv) &&
    /(?:^|\n)OMBRE_WRITE_ENABLED=false(?:\n|$)/.test(xinchaoEnv));
  ok &&= check('vNext OB template requires MCP auth and isolated paths',
    /(?:^|\n)OMBRE_MCP_REQUIRE_AUTH=true(?:\n|$)/.test(obEnv) &&
    /(?:^|\n)OMBRE_BUCKETS_DIR=\/app\/buckets(?:\n|$)/.test(obEnv) &&
    /(?:^|\n)OMBRE_CONFIG_PATH=\/app\/buckets\/config\.yaml(?:\n|$)/.test(obEnv));

  ok &&= assertNoProductionHosts('vNext templates contain no production host', `${obEnv}\n${xinchaoEnv}`);
  ok &&= check('deployment guide names the public Xinchao boundary',
    contains(deployReadme, 'Only the Xinchao service should receive the public HTTPS domain'));

  if (!ok) {
    console.error('vNext topology guard failed. No deployment or data mutation was attempted.');
    process.exitCode = 1;
  } else {
    console.log('vNext topology guard passed. This is an offline safety check, not deployment acceptance.');
  }
}

main();