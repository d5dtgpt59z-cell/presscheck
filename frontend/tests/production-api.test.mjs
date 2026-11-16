import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { stripTypeScriptTypes } from 'node:module';

const source = readFileSync(new URL('../app/production/api.ts', import.meta.url), 'utf8');
const compiled = stripTypeScriptTypes(source, { mode: 'strip' });
let instance = 0;
const freshAPI = () => import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}#${++instance}`);
function browser(href, store = new Map()) {
  globalThis.window = {
    location: { href },
    history: { replaceState(_state, _title, path) { this.lastPath = path; window.location.href = new URL(path, href).href; } },
    sessionStorage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) },
  };
  return store;
}
const token = 'test_desktop_access_01234567890123456789';

test('desktop authorization is captured, removed from URL and restored on same-origin reload', async () => {
  const store = browser(`http://127.0.0.1:51234/production/?token=${token}&view=report#findings`);
  const api = await freshAPI();
  assert.equal(api.captureDesktopToken(), true);
  assert.equal(window.history.lastPath, '/production/?view=report#findings');
  assert.equal(store.get('presscheck.desktop.token.v1'), token);
  browser('http://127.0.0.1:51234/production/', store);
  assert.equal((await freshAPI()).captureDesktopToken(), true);
});

test('API transport authenticates multipart and blob requests without putting token into body', async () => {
  browser(`http://127.0.0.1:51234/production/?token=${token}`);
  const api = await freshAPI();
  const body = new FormData(); body.set('job', '{"schema_version":1}');
  const requests = [];
  globalThis.fetch = async (path, options) => { requests.push({ path, options }); return new Response('png', { headers: { 'content-type': 'image/png' } }); };
  await api.productionFetch('/api/preflights', { method: 'POST', body });
  const response = await api.productionFetch('/api/preflights/abc/preview.png');
  assert.equal(await (await response.blob()).text(), 'png');
  for (const request of requests) {
    assert.equal(request.options.headers.get('Authorization'), `Bearer ${token}`);
    assert.equal(request.options.redirect, 'error');
    assert.equal(request.options.credentials, 'same-origin');
  }
  assert.equal(requests[0].options.body, body);
  assert.equal(requests[0].options.headers.has('Content-Type'), false);
  assert.ok(![...body.values()].join('').includes(token));
});

test('API transport refuses external and ambiguous destinations before fetch', async () => {
  browser('http://127.0.0.1:51234/production/');
  const api = await freshAPI();
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response('{}'); };
  for (const address of ['https://example.com/api/preflights', '//example.com/api/preflights', '/api/\\\\example.com', '/not-api']) {
    await assert.rejects(api.productionFetch(address), /Invalid local API address/);
  }
  assert.equal(calls, 0);
});

test('browser development needs no token and server errors remain meaningful', async () => {
  browser('http://127.0.0.1:3000/production/');
  const api = await freshAPI();
  globalThis.fetch = async (_path, options) => {
    assert.equal(options.headers.has('Authorization'), false);
    return new Response('{"detail":"Unsupported SVG external reference"}', { status: 422 });
  };
  await assert.rejects(api.productionJSON('/api/preflights'), /Unsupported SVG external reference/);
  globalThis.fetch = async () => new Response('{}', { status: 401 });
  await assert.rejects(api.productionFetch('/api/preflights'), /Reopen PressCheck Studio/);
});

test('invalid URL tokens fail and blocked storage can retain a valid token in memory', async () => {
  browser('http://127.0.0.1:51234/production/?token=bad');
  assert.throws((await freshAPI()).captureDesktopToken, /invalid access token/);
  browser(`http://127.0.0.1:51234/production/?token=${token}`);
  window.sessionStorage.setItem = () => { throw new Error('Storage blocked'); };
  const api = await freshAPI();
  assert.equal(api.captureDesktopToken(), true);
  assert.equal(api.captureDesktopToken(), true);
});
