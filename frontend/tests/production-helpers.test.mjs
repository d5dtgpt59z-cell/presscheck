import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultJob, normalizeJob, parseProfiles, exportProfiles, convertLength, parseLength } from '../app/production/profiles.mjs';

const wrapper = profiles => JSON.stringify({ schema_version: 1, profiles });
const profile = (job = {}) => ({ id: 'printer-1', name: 'Unverified printer specification', job });

test('a new specification preserves unknown dimensions and unconfirmed supplier intent', () => {
  const first = defaultJob();
  assert.equal(first.name, 'Custom / unverified specification');
  assert.equal(first.workflow, 'trading_card');
  assert.equal(first.schema_version, 1);
  assert.equal(first.bleed_intent, 'full_bleed');
  assert.equal(first.color_policy, 'review_rgb');
  assert.equal(first.supplier_confirmed, false);
  assert.equal(first.placement, 'fit_trim');
  for (const key of ['trim_width_mm', 'trim_height_mm', 'bleed_mm', 'safe_mm', 'min_text_pt', 'min_stroke_mm', 'min_raster_ppi']) {
    assert.equal(first[key], null, key);
    assert.equal(normalizeJob({})[key], null, key);
  }
  first.expected_roles.push('foil');
  assert.deepEqual(defaultJob().expected_roles, []);
  assert.equal(normalizeJob({ trim_width_mm: 63.5 }).trim_height_mm, null);
});

test('job validation rejects nonfinite, coerced, negative and excessive measurements', () => {
  const bounds = { trim_width_mm: 5000, trim_height_mm: 5000, bleed_mm: 300, safe_mm: 300, min_text_pt: 200, min_stroke_mm: 100, min_raster_ppi: 5000 };
  for (const [key, maximum] of Object.entries(bounds)) {
    assert.equal(normalizeJob({ [key]: maximum })[key], maximum);
    assert.equal(normalizeJob({ [key]: null })[key], null);
    for (const value of [NaN, Infinity, -Infinity, -1, maximum + 0.1, '3', true, undefined]) {
      assert.throws(() => normalizeJob({ [key]: value }), new RegExp(key), `${key}: ${String(value)}`);
    }
    if (['bleed_mm', 'safe_mm'].includes(key)) assert.equal(normalizeJob({ [key]: 0 })[key], 0);
    else assert.throws(() => normalizeJob({ [key]: 0 }), new RegExp(key));
  }
});

test('job choices and supplied strings must be explicit supported values', () => {
  for (const input of [null, [], 'job', 7, new Date()]) assert.throws(() => normalizeJob(input));
  for (const input of [{ schema_version: 2 }, { workflow: 'poster' }, { bleed_intent: 'unknown' }, { color_policy: 'auto' }, { placement: 'actual_size' }, { supplier_confirmed: 'false' }, { name: 'x'.repeat(161) }, { profile_name: null }, { printer_notes: 'x'.repeat(10001) }, { spec_source: 'x'.repeat(2001) }]) {
    assert.throws(() => normalizeJob(input));
  }
  assert.equal(normalizeJob({ workflow: 'packaging', color_policy: 'cmyk', bleed_intent: 'white_border', supplier_confirmed: true }).supplier_confirmed, true);
});

test('known native metadata and production roles survive without preserving arbitrary extras', () => {
  const input = {
    placement: 'fill_bleed', profile_name: 'Printer supplied — Nov 16',
    printer_notes: 'Check stock\nConfirm varnish coverage.', spec_source: 'Supplier PDF, revision 3',
    expected_roles: ['cut', 'crease', 'white_ink', 'foil', 'varnish', 'barcode'],
    arbitrary: { retained: false }, api_key: 'not a job field',
  };
  const job = normalizeJob(input);
  for (const key of ['placement', 'profile_name', 'printer_notes', 'spec_source']) assert.equal(job[key], input[key]);
  assert.deepEqual(job.expected_roles, input.expected_roles);
  assert.equal(Object.hasOwn(job, 'arbitrary'), false);
  assert.equal(Object.hasOwn(job, 'api_key'), false);
  job.expected_roles.push('emboss');
  assert.equal(input.expected_roles.includes('emboss'), false);
  for (const roles of [['unknown'], ['foil', 'foil'], 'cut', [null], Array(1), Array(31).fill('cut')]) {
    assert.throws(() => normalizeJob({ expected_roles: roles }), /expected_roles/);
  }
});

test('profile import/export round trips exact millimetres and native metadata', () => {
  const original = [{ ...profile({ trim_width_mm: 63.500123456789, bleed_mm: 0, placement: 'fill_bleed', expected_roles: ['white_ink'], spec_source: 'Manual specification' }), ignored: 'drop' }];
  const serialized = exportProfiles(original);
  assert.ok(serialized.startsWith('{\n  "schema_version": 1,'));
  const imported = parseProfiles(serialized);
  assert.equal(imported[0].job.trim_width_mm, 63.500123456789);
  assert.equal(imported[0].job.trim_height_mm, null);
  assert.equal(imported[0].job.bleed_mm, 0);
  assert.equal(imported[0].job.placement, 'fill_bleed');
  assert.equal(imported[0].job.spec_source, 'Manual specification');
  assert.equal(Object.hasOwn(imported[0], 'ignored'), false);
  assert.deepEqual(parseProfiles(exportProfiles(imported)), imported);
});

test('profile import rejects unsupported wrappers, unsafe IDs, duplicate IDs and missing jobs', () => {
  for (const value of ['', '[]', 'null', '{}', '{', '{"schema_version":2,"profiles":[]}', '{"schema_version":"1","profiles":[]}']) {
    assert.throws(() => parseProfiles(value));
  }
  for (const id of ['__proto__', 'prototype', 'constructor', 'toString', 'a/b', '', 'x'.repeat(81), 12]) {
    assert.throws(() => parseProfiles(wrapper([{ ...profile(), id }])), /ID/);
  }
  assert.throws(() => parseProfiles(wrapper([profile(), profile()])), /Duplicate/);
  assert.throws(() => parseProfiles(wrapper([{ id: 'a', name: 'A' }])));
  for (const name of ['', '   ', 'x'.repeat(161), 12]) assert.throws(() => parseProfiles(wrapper([{ ...profile(), name }])));
  assert.throws(() => parseProfiles(wrapper([profile({ trim_width_mm: '63.5' })])));
  assert.deepEqual(parseProfiles(wrapper([])), []);
  assert.throws(() => exportProfiles(Array(1)), /object/);
});

test('import/export limits bound profile count and UTF-8 bytes', () => {
  const hundred = Array.from({ length: 100 }, (_, i) => ({ ...profile(), id: `printer-${i}` }));
  assert.equal(parseProfiles(wrapper(hundred)).length, 100);
  assert.throws(() => parseProfiles(wrapper([...hundred, { ...profile(), id: 'extra' }])), /100/);
  assert.throws(() => exportProfiles([...hundred, { ...profile(), id: 'extra' }]), /100/);
  assert.throws(() => parseProfiles(' '.repeat(1024 * 1024 + 1)), /1 MiB/);
  const unicode = wrapper([{ ...profile(), job: { printer_notes: '🖨'.repeat(262144) } }]);
  assert.ok(unicode.length < 1024 * 1024);
  assert.throws(() => parseProfiles(unicode), /1 MiB/);
  const largeExport = hundred.map(item => ({ ...item, job: { printer_notes: '🖨'.repeat(5000) } }));
  assert.throws(() => exportProfiles(largeExport), /1 MiB/);
});

test('unit helpers distinguish blanks and invalid input from zero without rounding stored millimetres', () => {
  assert.equal(convertLength(63.5, 'in'), 2.5);
  assert.equal(parseLength('2.5', 'in'), 63.5);
  assert.equal(parseLength('0', 'mm'), 0);
  assert.equal(convertLength(null, 'in'), null);
  for (const input of ['', ' ', null, undefined, 'NaN', 'Infinity', '2mm', '0x10', '-2', NaN, Infinity, {}, '1e999']) {
    assert.equal(parseLength(input, 'mm'), null, String(input));
  }
  assert.equal(parseLength('  .125  ', 'in'), 3.175);
  assert.equal(parseLength('1e2', 'mm'), 100);
  const exact = 63.500123456789;
  const inches = convertLength(exact, 'in');
  assert.equal(inches, exact / 25.4);
  assert.ok(Math.abs(parseLength(String(inches), 'in') - exact) < 1e-12);
  assert.equal(normalizeJob({ trim_width_mm: exact }).trim_width_mm, exact);
  assert.throws(() => parseLength('1', 'cm'), /unit/);
});


test('saved and imported profiles never carry supplier confirmation into a new job', () => {
  const confirmed = profile({ trim_width_mm: 63.5, supplier_confirmed: true });
  assert.equal(normalizeJob(confirmed.job).supplier_confirmed, true, 'explicit confirmation is valid for the current job');
  assert.equal(parseProfiles(wrapper([confirmed]))[0].job.supplier_confirmed, false);
  assert.equal(JSON.parse(exportProfiles([confirmed])).profiles[0].job.supplier_confirmed, false);
});
