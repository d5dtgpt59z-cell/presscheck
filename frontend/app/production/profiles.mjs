// Pure helpers: printer specifications stay explicit; no storage or inferred presets.
const MAX_IMPORT_BYTES = 1024 * 1024;
const MAX_PROFILES = 100;
const ROLES = new Set([
  'artwork', 'cut', 'crease', 'perforation', 'glue', 'white', 'foil',
  'white_ink', 'varnish', 'emboss', 'deboss', 'dimensions', 'registration',
  'barcode', 'legend',
]);
const NUMBER_FIELDS = {
  trim_width_mm: [0, 5000, false],
  trim_height_mm: [0, 5000, false],
  bleed_mm: [0, 300, true],
  safe_mm: [0, 300, true],
  min_text_pt: [0, 200, false],
  min_stroke_mm: [0, 100, false],
  min_raster_ppi: [0, 5000, false],
};
const ENUM_FIELDS = {
  workflow: ['trading_card', 'packaging'],
  bleed_intent: ['full_bleed', 'white_border'],
  color_policy: ['review_rgb', 'rgb', 'cmyk'],
  placement: ['fit_trim', 'fill_bleed'],
};
const TEXT_FIELDS = { name: 160, profile_name: 160, printer_notes: 10000, spec_source: 2000 };
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

function object(value, label) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)
      || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    throw new Error(`${label} must be an object.`);
  }
  return value;
}

function text(value, maximum, label, required = false) {
  if (typeof value !== 'string' || value.length > maximum
      || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)
      || (required && !value.trim())) {
    throw new Error(`${label} must be ${required ? 'nonempty ' : ''}text of at most ${maximum} characters.`);
  }
  return value;
}

export function defaultJob() {
  return {
    schema_version: 1,
    workflow: 'trading_card',
    name: 'Custom / unverified specification',
    trim_width_mm: null,
    trim_height_mm: null,
    bleed_mm: null,
    safe_mm: null,
    bleed_intent: 'full_bleed',
    color_policy: 'review_rgb',
    min_text_pt: null,
    min_stroke_mm: null,
    min_raster_ppi: null,
    expected_roles: [],
    supplier_confirmed: false,
    placement: 'fit_trim',
    profile_name: '',
    printer_notes: '',
    spec_source: '',
  };
}

export function normalizeJob(input) {
  object(input, 'Job');
  const job = defaultJob();
  if (own(input, 'schema_version') && input.schema_version !== 1) {
    throw new Error('Unsupported job schema version; expected 1.');
  }
  for (const [key, options] of Object.entries(ENUM_FIELDS)) {
    if (!own(input, key)) continue;
    if (!options.includes(input[key])) throw new Error(`Invalid ${key}.`);
    job[key] = input[key];
  }
  for (const [key, [minimum, maximum, includesMinimum]] of Object.entries(NUMBER_FIELDS)) {
    if (!own(input, key)) continue;
    const value = input[key];
    if (value !== null && (typeof value !== 'number' || !Number.isFinite(value)
        || value > maximum || (includesMinimum ? value < minimum : value <= minimum))) {
      throw new Error(`${key} must be unknown or a finite number ${includesMinimum ? 'at least' : 'greater than'} ${minimum} and at most ${maximum}.`);
    }
    job[key] = value;
  }
  for (const [key, maximum] of Object.entries(TEXT_FIELDS)) {
    if (own(input, key)) job[key] = text(input[key], maximum, key);
  }
  if (own(input, 'supplier_confirmed')) {
    if (typeof input.supplier_confirmed !== 'boolean') throw new Error('supplier_confirmed must be true or false.');
    job.supplier_confirmed = input.supplier_confirmed;
  }
  if (own(input, 'expected_roles')) {
    const roles = input.expected_roles;
    if (!Array.isArray(roles) || roles.length > 30 || Array.from(roles).some(role => !ROLES.has(role))) {
      throw new Error('expected_roles must contain at most 30 recognized production roles.');
    }
    if (new Set(roles).size !== roles.length) throw new Error('expected_roles must not contain duplicate roles.');
    job.expected_roles = [...roles];
  }
  return job;
}

function normalizeProfiles(input) {
  if (!Array.isArray(input) || input.length > MAX_PROFILES) {
    throw new Error(`Profiles must be a list of at most ${MAX_PROFILES} entries.`);
  }
  const seen = new Set();
  return Array.from(input, (value, index) => {
    const profile = object(value, `Profile ${index + 1}`);
    const id = profile.id;
    if (typeof id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(id)
        || id === 'prototype' || own(Object.prototype, id)) {
      throw new Error(`Profile ${index + 1} has an invalid ID.`);
    }
    if (seen.has(id)) throw new Error(`Duplicate profile ID: ${id}.`);
    seen.add(id);
    return { id, name: text(profile.name, 160, `Profile ${index + 1} name`, true), job: { ...normalizeJob(profile.job), supplier_confirmed: false } };
  });
}

export function parseProfiles(source) {
  if (typeof source !== 'string') throw new Error('Profile import must be JSON text.');
  if (new TextEncoder().encode(source).byteLength > MAX_IMPORT_BYTES) {
    throw new Error('Profile import exceeds the 1 MiB limit.');
  }
  let imported;
  try { imported = JSON.parse(source); }
  catch { throw new Error('Profile import is not valid JSON.'); }
  object(imported, 'Profile import');
  if (imported.schema_version !== 1) throw new Error('Unsupported profile schema version; expected 1.');
  return normalizeProfiles(imported.profiles);
}

export function exportProfiles(profiles) {
  const result = JSON.stringify({ schema_version: 1, profiles: normalizeProfiles(profiles) }, null, 2) + '\n';
  if (new TextEncoder().encode(result).byteLength > MAX_IMPORT_BYTES) {
    throw new Error('Profile export exceeds the 1 MiB limit.');
  }
  return result;
}

function checkUnit(unit) {
  if (unit !== 'mm' && unit !== 'in') throw new Error('Length unit must be mm or in.');
}

export function convertLength(value, unit = 'mm') {
  checkUnit(unit);
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  return unit === 'in' ? value / 25.4 : value;
}

export function parseLength(raw, unit = 'mm') {
  checkUnit(unit);
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  const input = String(raw).trim();
  if (!/^[+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(input)) return null;
  const value = Number(input) * (unit === 'in' ? 25.4 : 1);
  return Number.isFinite(value) && value >= 0 ? value : null;
}
