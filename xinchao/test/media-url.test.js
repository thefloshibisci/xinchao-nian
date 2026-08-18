import test from 'node:test';
import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import {
  downloadImageFromUrl,
  isPublicAddress,
  prepareObMediaArgs,
  readBoundedResponse,
} from '../src/media-url.js';

function response(statusCode, headers = {}, chunks = []) {
  const stream = Readable.from(chunks);
  stream.statusCode = statusCode;
  stream.headers = headers;
  return stream;
}

test('converts URL media while preserving existing media forms', async () => {
  const args = {
    media: [
      { url: 'https://images.example.test/cat', title: '小猫', note: '留念' },
      { data_base64: 'eA==', filename: 'old.png' },
      '/server/readable/photo.jpg',
    ],
    content: '正文',
  };
  const prepared = await prepareObMediaArgs('hold', args, {
    download: async () => ({
      data: Buffer.from('image-bytes'),
      mimeType: 'image/png',
      finalUrl: new URL('https://cdn.example.test/cat'),
    }),
  });

  assert.notEqual(prepared, args);
  assert.deepEqual(prepared.media[0], {
    title: '小猫',
    note: '留念',
    data_base64: Buffer.from('image-bytes').toString('base64'),
    filename: 'cat.png',
    type: 'image/png',
  });
  assert.equal(prepared.media[1], args.media[1]);
  assert.equal(prepared.media[2], args.media[2]);
  assert.equal(prepared.content, '正文');
});

test('supports URL strings in both trace media fields', async () => {
  const download = async () => ({
    data: Buffer.from('jpeg'),
    mimeType: 'image/jpeg',
    finalUrl: new URL('https://example.com/final.jpg'),
  });
  const prepared = await prepareObMediaArgs('trace', {
    media_append: 'https://example.com/a',
    media_replace: ['https://example.com/b'],
  }, { download });
  assert.equal(prepared.media_append.filename, 'final.jpg');
  assert.equal(prepared.media_replace[0].type, 'image/jpeg');
});

test('shares the download size budget across both trace media fields', async () => {
  const download = async () => ({
    data: Buffer.alloc(3),
    mimeType: 'image/jpeg',
    finalUrl: new URL('https://example.com/photo.jpg'),
  });
  await assert.rejects(prepareObMediaArgs('trace', {
    media_append: 'https://example.com/a',
    media_replace: 'https://example.com/b',
  }, { download, maxBytes: 5 }), /总大小超过/);
});

test('does not touch non-media OB tools', async () => {
  const args = { media: ['https://example.com/image.png'] };
  assert.equal(await prepareObMediaArgs('grow', args), args);
});

test('recognizes public and blocked address ranges', () => {
  assert.equal(isPublicAddress('8.8.8.8'), true);
  assert.equal(isPublicAddress('2606:4700:4700::1111'), true);
  for (const address of ['127.0.0.1', '10.0.0.1', '169.254.169.254', '192.168.1.1', '::1', 'fc00::1', 'fe80::1', '::127.0.0.1', '::ffff:127.0.0.1']) {
    assert.equal(isPublicAddress(address), false, address);
  }
});

test('rejects private DNS results and non-http protocols before requesting', async () => {
  let requested = false;
  const options = {
    lookup: async () => [{ address: '10.0.0.2', family: 4 }],
    requestOnce: async () => { requested = true; },
  };
  await assert.rejects(downloadImageFromUrl('https://example.test/a.png', options), /内网|保留/);
  await assert.rejects(downloadImageFromUrl('file:///tmp/a.png', options), /http/);
  assert.equal(requested, false);
});

test('rejects private literal IPv6 URLs without a DNS lookup', async () => {
  let lookedUp = false;
  let requested = false;
  await assert.rejects(downloadImageFromUrl('http://[::1]/a.png', {
    lookup: async () => { lookedUp = true; return []; },
    requestOnce: async () => { requested = true; },
  }), /内网|保留/);
  assert.equal(lookedUp, false);
  assert.equal(requested, false);
});

test('checks every redirect target again', async () => {
  const lookedUp = [];
  const lookup = async (hostname) => {
    lookedUp.push(hostname);
    return hostname === 'public.example'
      ? [{ address: '8.8.8.8', family: 4 }]
      : [{ address: '127.0.0.1', family: 4 }];
  };
  let calls = 0;
  const requestOnce = async () => {
    calls += 1;
    return response(302, { location: 'http://private.example/secret.png' });
  };
  await assert.rejects(downloadImageFromUrl('https://public.example/start', { lookup, requestOnce }), /内网|保留/);
  assert.deepEqual(lookedUp, ['public.example', 'private.example']);
  assert.equal(calls, 1);
});

test('rejects non-image, spoofed image, and streamed bodies over the limit', async () => {
  const lookup = async () => [{ address: '8.8.8.8', family: 4 }];
  await assert.rejects(downloadImageFromUrl('https://example.test/a', {
    lookup,
    requestOnce: async () => response(200, { 'content-type': 'text/html' }, ['nope']),
  }), /不是受支持图片/);

  await assert.rejects(downloadImageFromUrl('https://example.test/fake.png', {
    lookup,
    requestOnce: async () => response(200, { 'content-type': 'image/png' }, ['not really a png']),
  }), /类型不符/);

  await assert.rejects(readBoundedResponse(
    response(200, { 'content-type': 'image/png' }, [Buffer.alloc(3), Buffer.alloc(3)]),
    5,
  ), /超过/);
});
