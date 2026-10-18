// Shell-script tests in throw-away dirs. No opencode, claude or Chrome is started
// (OPENCODE_BIN=true; npm is stubbed for the install scripts).
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const scripts = path.join(root, 'scripts');
const WRAPPER = path.join(scripts, 'chrome-devtools-wrapper.sh');
const tmp = () => mkdtempSync(path.join(os.tmpdir(), 'bb-oc-'));

const env = (skillSrc, extra = {}) => ({ ...process.env, OPENCODE_BIN: 'true', BROWSERBASE_SKILL_SRC: skillSrc, ...extra });
const run = (script, args, e) => spawnSync('bash', [path.join(scripts, script), ...args], { env: e, encoding: 'utf8' });
const git = (cwd, ...a) => execFileSync('git', ['-C', cwd, ...a], { encoding: 'utf8' }).trim();
const ours = { type: 'local', command: [WRAPPER], enabled: true, timeout: 60000 };
const readJson = (f) => JSON.parse(readFileSync(f, 'utf8'));

function fixture() {
  const base = tmp();
  const skill = path.join(base, 'vendored-skill');
  mkdirSync(skill);
  writeFileSync(path.join(skill, 'SKILL.md'), '---\nname: chrome-devtools\ndescription: x\n---\n');
  return { base, skill, e: env(skill) };
}
function repo(base, name) {
  const d = path.join(base, name);
  mkdirSync(d);
  git(d, 'init', '-q');
  git(d, 'config', 'user.email', 't@example.test'); git(d, 'config', 'user.name', 't');
  writeFileSync(path.join(d, 'README'), 'x');
  git(d, 'add', '.'); git(d, 'commit', '-qm', 'init');
  return d;
}

test('plain dir: creates config + skill link, idempotent, --remove restores', () => {
  const { base, skill, e } = fixture();
  const d = path.join(base, 'proj'); mkdirSync(d);
  for (let i = 0; i < 2; i++) assert.equal(run('browser-opencode.sh', [d], e).status, 0);
  assert.deepEqual(readJson(path.join(d, 'opencode.json')), { mcp: { 'chrome-devtools': ours } });
  assert.equal(readlinkSync(path.join(d, '.opencode/skills/chrome-devtools')), skill);
  assert.equal(run('browser-opencode.sh', ['--remove', d], e).status, 0);
  assert.equal(execFileSync('ls', ['-A', d], { encoding: 'utf8' }).trim(), '');
  rmSync(base, { recursive: true });
});

test('existing opencode.json: other keys kept, file survives --remove unchanged', () => {
  const { base, e } = fixture();
  const d = path.join(base, 'proj'); mkdirSync(d);
  const orig = { $schema: 'https://opencode.ai/config.json', update: 'disable', permission: { '*': 'allow', bash: { '*': 'deny' } }, mcp: { other: { type: 'remote', url: 'http://127.0.0.1:1/x' } } };
  writeFileSync(path.join(d, 'opencode.json'), JSON.stringify(orig));
  assert.equal(run('browser-opencode.sh', [d], e).status, 0);
  assert.deepEqual(readJson(path.join(d, 'opencode.json')), { ...orig, mcp: { ...orig.mcp, 'chrome-devtools': ours } });
  assert.equal(run('browser-opencode.sh', ['--remove', d], e).status, 0);
  assert.deepEqual(readJson(path.join(d, 'opencode.json')), orig);
  rmSync(base, { recursive: true });
});

test('refuses to overwrite a foreign mcp.chrome-devtools', () => {
  const { base, e } = fixture();
  const d = path.join(base, 'proj'); mkdirSync(d);
  const orig = { mcp: { 'chrome-devtools': { type: 'local', command: ['/bin/other'] } } };
  writeFileSync(path.join(d, 'opencode.json'), JSON.stringify(orig));
  assert.notEqual(run('browser-opencode.sh', [d], e).status, 0);
  assert.deepEqual(readJson(path.join(d, 'opencode.json')), orig);
  rmSync(base, { recursive: true });
});

for (const kind of ['repo', 'worktree', 'subdir of worktree']) {
  test(`git ${kind}: git status stays clean, exclude restored by --remove`, () => {
    const { base, e } = fixture();
    const main = repo(base, 'main');
    const wt = path.join(base, 'wt');
    let d = main;
    if (kind !== 'repo') { git(main, 'worktree', 'add', '-q', '-b', 'feat', wt); d = wt; }
    if (kind === 'subdir of worktree') { d = path.join(wt, 'sub'); mkdirSync(d); }
    const excludeFile = path.resolve(d, git(d, 'rev-parse', '--git-path', 'info/exclude'));
    const before = existsSync(excludeFile) ? readFileSync(excludeFile, 'utf8') : '';

    for (let i = 0; i < 2; i++) assert.equal(run('browser-opencode.sh', [d], e).status, 0);
    assert.equal(git(d, 'status', '--porcelain', '-uall'), '', 'git status must be clean');
    const lines = readFileSync(excludeFile, 'utf8').split('\n');
    assert.equal(lines.filter((l) => l.endsWith('opencode.json')).length, 1, 'no duplicate exclude lines');

    assert.equal(run('browser-opencode.sh', ['--remove', d], e).status, 0);
    assert.equal(existsSync(excludeFile) ? readFileSync(excludeFile, 'utf8') : '', before);
    assert.equal(git(d, 'status', '--porcelain', '-uall'), '');
    assert.equal(existsSync(path.join(d, 'opencode.json')), false);
    rmSync(base, { recursive: true, force: true });
  });
}

test('git: a pre-existing exclude line is not removed by --remove', () => {
  const { base, e } = fixture();
  const d = repo(base, 'main');
  const ex = path.join(d, '.git/info/exclude');
  writeFileSync(ex, '/opencode.json\n');
  run('browser-opencode.sh', [d], e);
  run('browser-opencode.sh', ['--remove', d], e);
  assert.equal(readFileSync(ex, 'utf8'), '/opencode.json\n');
  rmSync(base, { recursive: true, force: true });
});

test('git: tracked opencode.json is refused and left untouched', () => {
  const { base, e } = fixture();
  const d = repo(base, 'main');
  writeFileSync(path.join(d, 'opencode.json'), '{"a":1}\n');
  git(d, 'add', 'opencode.json'); git(d, 'commit', '-qm', 'track');
  const r = run('browser-opencode.sh', [d], e);
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /tracked/);
  assert.equal(readFileSync(path.join(d, 'opencode.json'), 'utf8'), '{"a":1}\n');
  assert.equal(git(d, 'status', '--porcelain'), '');
  rmSync(base, { recursive: true, force: true });
});

test('install-opencode.sh: removes only our mcp key + skill link', () => {
  const { base, skill, e } = fixture();
  const home = path.join(base, 'oc'); mkdirSync(path.join(home, 'skills'), { recursive: true });
  const stub = path.join(base, 'bin'); mkdirSync(stub);
  writeFileSync(path.join(stub, 'npm'), '#!/bin/sh\nexit 0\n'); chmodSync(path.join(stub, 'npm'), 0o755);
  const orig = { update: 'disable', permission: { '*': 'allow', external_directory: { [root]: 'allow' } }, mcp: { 'chrome-devtools': ours, other: { type: 'remote', url: 'http://127.0.0.1:1/x' } } };
  writeFileSync(path.join(home, 'opencode.json'), JSON.stringify(orig));
  symlinkSync(skill, path.join(home, 'skills/chrome-devtools'));
  symlinkSync('/nonexistent', path.join(home, 'skills/foreign'));
  const r = run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: `${stub}:${process.env.PATH}` });
  assert.equal(r.status, 0, r.stderr);
  const { 'chrome-devtools': _gone, ...rest } = orig.mcp;
  assert.deepEqual(readJson(path.join(home, 'opencode.json')), { ...orig, mcp: rest });
  assert.throws(() => lstatSync(path.join(home, 'skills/chrome-devtools')));
  assert.ok(lstatSync(path.join(home, 'skills/foreign')).isSymbolicLink());
  rmSync(base, { recursive: true, force: true });
});
