// Shell-script tests in throw-away dirs. No opencode, claude or Chrome is started:
// OPENCODE_BIN=true, and `claude`/`npm` are stubs placed first on PATH (checked with `command -v`).
// The install scripts still run update_vendor, which creates <repo>/user_profile and chmods the
// wrapper inside this repo; nothing else outside the temp dirs is touched.
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

// A stub bin dir first on PATH: npm (no-op) and claude (records argv NUL-separated; `mcp remove -s user
// <name>` deletes that key from $CLAUDE_CONFIG_DIR/.claude.json, like the real command would).
function stubs(base) {
  const bin = path.join(base, 'stubbin'); mkdirSync(bin);
  const put = (n, body) => { writeFileSync(path.join(bin, n), `#!/bin/sh\n${body}\n`); chmodSync(path.join(bin, n), 0o755); };
  put('npm', 'exit 0');
  put('claude', `printf '%s\\0' "$@" > "$STUB_ARGV"
if [ "$1 $2" = "mcp remove" ]; then
  f="$CLAUDE_CONFIG_DIR/.claude.json"; jq --arg n "$5" 'del(.mcpServers[$n])' "$f" > "$f.t" && mv "$f.t" "$f"
fi`);
  const PATH = `${bin}:${process.env.PATH}`;
  const resolved = execFileSync('bash', ['-c', 'command -v claude'], { env: { ...process.env, PATH }, encoding: 'utf8' }).trim();
  assert.equal(resolved, path.join(bin, 'claude'), 'claude must resolve to the stub');
  return { bin, PATH, argvFile: path.join(base, 'argv') };
}
const argvOf = (f) => readFileSync(f, 'utf8').split('\0').slice(0, -1);

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

// The manual cleanup snippet is taken from the README so the documented command is what gets tested.
const readme = readFileSync(path.join(root, 'README.md'), 'utf8');
const CLEANUP = readme.match(/```bash\n(f="\$\(git rev-parse --git-path info\/exclude\)"[\s\S]*?)```/)[1];
for (const kind of ['repo', 'worktree', 'subdir of worktree']) {
  test(`git ${kind}: status clean; --remove keeps exclude lines; README cleanup command restores`, () => {
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
    const afterAdd = readFileSync(excludeFile, 'utf8');
    assert.equal(afterAdd.split('\n').filter((l) => l.endsWith('opencode.json')).length, 1, 'no duplicate exclude lines');

    assert.equal(run('browser-opencode.sh', ['--remove', d], e).status, 0);
    assert.equal(readFileSync(excludeFile, 'utf8'), afterAdd, '--remove must not edit info/exclude');
    assert.equal(existsSync(path.join(d, 'opencode.json')), false);
    assert.equal(existsSync(path.join(d, '.opencode')), false);
    assert.equal(git(d, 'status', '--porcelain', '-uall'), '');

    // the manual cleanup command from the README restores the file
    if (kind !== 'subdir of worktree') {   // the README snippet is for the worktree-root lines
      execFileSync('bash', ['-c', CLEANUP], { cwd: d });
      assert.equal(readFileSync(excludeFile, 'utf8'), before);
    }
    rmSync(base, { recursive: true, force: true });
  });
}

test('git: a pre-existing exclude line is not duplicated', () => {
  const { base, e } = fixture();
  const d = repo(base, 'main');
  const ex = path.join(d, '.git/info/exclude');
  writeFileSync(ex, '/opencode.json\n');
  run('browser-opencode.sh', [d], e);
  run('browser-opencode.sh', ['--remove', d], e);
  assert.equal(readFileSync(ex, 'utf8').split('\n').filter((l) => l === '/opencode.json').length, 1);
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
  const st = stubs(base);
  const orig = { $schema: 'https://opencode.ai/config.json', update: 'disable', permission: { '*': 'allow', 'cdp_*': 'allow', bash: { '*': 'allow', '*/start_browser.sh': 'allow', '*.ssh*': 'deny' }, external_directory: { [root]: 'allow' } }, mcp: { 'chrome-devtools': ours, 'cdp-enh': { type: 'local', command: ['/x'] }, other: { type: 'remote', url: 'http://127.0.0.1:1/x' } } };
  writeFileSync(path.join(home, 'opencode.json'), JSON.stringify(orig));
  symlinkSync(skill, path.join(home, 'skills/chrome-devtools'));
  symlinkSync('/nonexistent', path.join(home, 'skills/foreign'));
  const r = run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: st.PATH });
  assert.equal(r.status, 0, r.stderr);
  const expected = structuredClone(orig);
  delete expected.mcp['chrome-devtools']; delete expected.mcp['cdp-enh'];
  delete expected.permission['cdp_*']; delete expected.permission.bash['*/start_browser.sh'];
  assert.deepEqual(readJson(path.join(home, 'opencode.json')), expected);
  assert.deepEqual(readJson(path.join(home, 'opencode.json')).permission.external_directory, { [root]: 'allow' }, 'external_directory is intentionally kept');
  assert.throws(() => lstatSync(path.join(home, 'skills/chrome-devtools')));
  assert.ok(lstatSync(path.join(home, 'skills/foreign')).isSymbolicLink());
  rmSync(base, { recursive: true, force: true });
});

test('install-opencode.sh: no-op on a config without our leftovers (no keys added)', () => {
  const { base, e } = fixture();
  const home = path.join(base, 'oc'); mkdirSync(home);
  const st = stubs(base);
  const orig = { update: 'disable', mcp: { other: { type: 'remote', url: 'http://127.0.0.1:1/x' } } };
  writeFileSync(path.join(home, 'opencode.json'), JSON.stringify(orig));
  assert.equal(run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: st.PATH }).status, 0);
  assert.deepEqual(readJson(path.join(home, 'opencode.json')), orig);
  const noPerm = { mcp: {} };
  writeFileSync(path.join(home, 'opencode.json'), JSON.stringify(noPerm));
  run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: st.PATH });
  assert.deepEqual(readJson(path.join(home, 'opencode.json')), noPerm, 'no permission key may appear');
  rmSync(base, { recursive: true, force: true });
});

test('install-claude.sh (stub claude): removes only our global entry + skill link', () => {
  const { base, skill, e } = fixture();
  const st = stubs(base);
  const home = path.join(base, 'home'); mkdirSync(path.join(home, 'skills'), { recursive: true });
  const cfgDir = home;
  const orig = { keep: 1, mcpServers: { 'chrome-devtools': { type: 'stdio', command: WRAPPER, args: [] }, other: { type: 'stdio', command: '/bin/other' } } };
  writeFileSync(path.join(cfgDir, '.claude.json'), JSON.stringify(orig));
  mkdirSync(path.join(home, '.claude/skills'), { recursive: true });
  symlinkSync(skill, path.join(home, '.claude/skills/chrome-devtools'));
  symlinkSync('/nonexistent', path.join(home, '.claude/skills/other'));
  const ee = { ...e, HOME: home, CLAUDE_CONFIG_DIR: cfgDir, PATH: st.PATH, STUB_ARGV: st.argvFile };
  const r = run('install-claude.sh', [], ee);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(argvOf(st.argvFile), ['mcp', 'remove', '-s', 'user', 'chrome-devtools']);
  assert.deepEqual(readJson(path.join(cfgDir, '.claude.json')), { keep: 1, mcpServers: { other: orig.mcpServers.other } });
  assert.throws(() => lstatSync(path.join(home, '.claude/skills/chrome-devtools')));
  assert.ok(lstatSync(path.join(home, '.claude/skills/other')).isSymbolicLink());
  rmSync(base, { recursive: true, force: true });
});

test('install-claude.sh (stub claude): a foreign chrome-devtools entry is left alone', () => {
  const { base, skill, e } = fixture();
  const st = stubs(base);
  const home = path.join(base, 'home'); mkdirSync(path.join(home, '.claude/skills'), { recursive: true });
  const orig = { mcpServers: { 'chrome-devtools': { type: 'stdio', command: '/bin/other', args: [] } } };
  writeFileSync(path.join(home, '.claude.json'), JSON.stringify(orig));
  symlinkSync('/nonexistent', path.join(home, '.claude/skills/chrome-devtools'));
  const r = run('install-claude.sh', [], { ...e, HOME: home, CLAUDE_CONFIG_DIR: home, PATH: st.PATH, STUB_ARGV: st.argvFile });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(existsSync(st.argvFile), false, 'claude must not be called');
  assert.deepEqual(readJson(path.join(home, '.claude.json')), orig);
  assert.ok(lstatSync(path.join(home, '.claude/skills/chrome-devtools')).isSymbolicLink());
  rmSync(base, { recursive: true, force: true });
});

test('browser-claude.sh (stub claude): --mcp-config goes after user args, before a literal --', () => {
  const { base, e } = fixture();
  const st = stubs(base);
  const ee = { ...e, PATH: st.PATH, STUB_ARGV: st.argvFile };
  const cases = [[], ['hello there'], ['-p', 'hi'], ['--model', 'x', 'prompt'], ['a', '--', 'b']];
  for (const args of cases) {
    assert.equal(run('browser-claude.sh', args, ee).status, 0);
    const argv = argvOf(st.argvFile);
    const i = argv.indexOf('--mcp-config');
    assert.ok(i >= 0, `no --mcp-config in ${JSON.stringify(argv)}`);
    assert.deepEqual(JSON.parse(argv[i + 1]), { mcpServers: { 'chrome-devtools': { command: WRAPPER, args: [] } } });
    assert.deepEqual([...argv.slice(0, i), ...argv.slice(i + 2)], args, 'user args preserved in order');
    const dd = args.indexOf('--');
    if (dd >= 0) assert.equal(i, dd, '--mcp-config must precede the literal --');
    else assert.equal(i, args.length, '--mcp-config comes last, so a bare prompt is never swallowed');
  }
  rmSync(base, { recursive: true, force: true });
});

test('browser-opencode.sh: existing non-symlink LINK is refused, nothing written', () => {
  const { base, e } = fixture();
  const d = path.join(base, 'proj'); mkdirSync(path.join(d, '.opencode/skills/chrome-devtools'), { recursive: true });
  const r = run('browser-opencode.sh', [d], e);
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /not ours/);
  assert.equal(existsSync(path.join(d, 'opencode.json')), false);
  assert.equal(existsSync(path.join(d, 'opencode.json.tmp')), false);
  rmSync(base, { recursive: true, force: true });
});

test('browser-opencode.sh: invalid JSON config -> fails, no tmp file, no .opencode created', () => {
  const { base, e } = fixture();
  const d = path.join(base, 'proj'); mkdirSync(d);
  writeFileSync(path.join(d, 'opencode.json'), '{broken');
  assert.notEqual(run('browser-opencode.sh', [d], e).status, 0);
  assert.equal(readFileSync(path.join(d, 'opencode.json'), 'utf8'), '{broken');
  assert.equal(existsSync(path.join(d, 'opencode.json.tmp')), false);
  assert.equal(existsSync(path.join(d, '.opencode')), false);
  rmSync(base, { recursive: true, force: true });
});

test('--remove only deletes our own entries (foreign mcp key / foreign link / user-added keys stay)', () => {
  const { base, e } = fixture();
  const d = path.join(base, 'proj'); mkdirSync(d);
  assert.equal(run('browser-opencode.sh', [d], e).status, 0);
  // user adds a key to the file we created; and replaces the link with their own
  const cfg = readJson(path.join(d, 'opencode.json')); cfg.theme = 'x';
  writeFileSync(path.join(d, 'opencode.json'), JSON.stringify(cfg));
  assert.equal(run('browser-opencode.sh', ['--remove', d], e).status, 0);
  assert.deepEqual(readJson(path.join(d, 'opencode.json')), { theme: 'x' }, 'file we created is kept once it holds user keys');

  const d2 = path.join(base, 'proj2'); mkdirSync(path.join(d2, '.opencode/skills'), { recursive: true });
  symlinkSync('/nonexistent', path.join(d2, '.opencode/skills/chrome-devtools'));
  writeFileSync(path.join(d2, 'opencode.json'), JSON.stringify({ mcp: { 'chrome-devtools': { type: 'local', command: ['/bin/other'] } } }));
  assert.equal(run('browser-opencode.sh', ['--remove', d2], e).status, 0);
  assert.equal(readJson(path.join(d2, 'opencode.json')).mcp['chrome-devtools'].command[0], '/bin/other');
  assert.equal(readlinkSync(path.join(d2, '.opencode/skills/chrome-devtools')), '/nonexistent');
  rmSync(base, { recursive: true, force: true });
});

test('worktrees: --remove in one leaves exclude lines, so the other worktree stays clean', () => {
  const { base, e } = fixture();
  const main = repo(base, 'main');
  const w1 = path.join(base, 'w1'), w2 = path.join(base, 'w2');
  git(main, 'worktree', 'add', '-q', '-b', 'b1', w1); git(main, 'worktree', 'add', '-q', '-b', 'b2', w2);
  assert.equal(run('browser-opencode.sh', [w1], e).status, 0);
  assert.equal(run('browser-opencode.sh', [w2], e).status, 0);
  assert.equal(run('browser-opencode.sh', ['--remove', w1], e).status, 0);
  assert.equal(git(w2, 'status', '--porcelain', '-uall'), '', 'w2 must stay clean after w1 --remove');
  assert.ok(existsSync(path.join(w2, 'opencode.json')));
  // even if the owner worktree is deleted, nothing becomes exposed in the others
  git(main, 'worktree', 'remove', '--force', w1);
  assert.equal(git(w2, 'status', '--porcelain', '-uall'), '');
  rmSync(base, { recursive: true, force: true });
});

test('known trade-off: a same-path untracked opencode.json elsewhere in the repo is hidden too', () => {
  const { base, e } = fixture();
  const main = repo(base, 'main');
  const wt = path.join(base, 'wt');
  git(main, 'worktree', 'add', '-q', '-b', 'f', wt);
  writeFileSync(path.join(main, 'opencode.json'), '{"mine":true}\n');
  assert.match(git(main, 'status', '--porcelain'), /\?\? opencode\.json/);
  assert.equal(run('browser-opencode.sh', [wt], e).status, 0);
  assert.equal(git(main, 'status', '--porcelain'), '', 'documented side effect of the shared exclude');
  // a subdirectory run is anchored to that path and does not hide the root file
  const main2 = repo(base, 'main2'); writeFileSync(path.join(main2, 'opencode.json'), '{}');
  const sub = path.join(main2, 'sub'); mkdirSync(sub);
  assert.equal(run('browser-opencode.sh', [sub], e).status, 0);
  assert.match(git(main2, 'status', '--porcelain'), /\?\? opencode\.json/);
  rmSync(base, { recursive: true, force: true });
});

test('user\'s own opencode.json.tmp is never deleted (add, --remove, install-opencode)', () => {
  const { base, e } = fixture();
  const d = path.join(base, 'proj'); mkdirSync(d);
  writeFileSync(path.join(d, 'opencode.json.tmp'), 'mine');
  assert.equal(run('browser-opencode.sh', [d], e).status, 0);
  assert.equal(run('browser-opencode.sh', ['--remove', d], e).status, 0);
  assert.equal(readFileSync(path.join(d, 'opencode.json.tmp'), 'utf8'), 'mine');
  // failure path (invalid JSON) too
  writeFileSync(path.join(d, 'opencode.json'), '{broken');
  assert.notEqual(run('browser-opencode.sh', [d], e).status, 0);
  assert.equal(readFileSync(path.join(d, 'opencode.json.tmp'), 'utf8'), 'mine');
  assert.deepEqual(execFileSync('ls', ['-A', d], { encoding: 'utf8' }).trim().split('\n').sort(), ['opencode.json', 'opencode.json.tmp']);

  const st = stubs(base);
  const home = path.join(base, 'oc'); mkdirSync(home);
  writeFileSync(path.join(home, 'opencode.json'), JSON.stringify({ mcp: { 'cdp-enh': {} } }));
  writeFileSync(path.join(home, 'opencode.json.tmp'), 'mine');
  assert.equal(run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: st.PATH }).status, 0);
  assert.equal(readFileSync(path.join(home, 'opencode.json.tmp'), 'utf8'), 'mine');
  assert.deepEqual(readJson(path.join(home, 'opencode.json')), { mcp: {} });
  assert.deepEqual(execFileSync('ls', ['-A', home], { encoding: 'utf8' }).trim().split('\n').sort(), ['opencode.json', 'opencode.json.tmp']);
  rmSync(base, { recursive: true, force: true });
});

test('--remove: empty mcp only dropped when state says we introduced it; file deleted only if we created it', () => {
  const { base, e } = fixture();
  // not ours (no state): the user's own {"mcp":{}} is left exactly as it is
  const d1 = path.join(base, 'p1'); mkdirSync(d1);
  writeFileSync(path.join(d1, 'opencode.json'), '{"mcp":{}}');
  assert.equal(run('browser-opencode.sh', ['--remove', d1], e).status, 0);
  assert.equal(readFileSync(path.join(d1, 'opencode.json'), 'utf8'), '{"mcp":{}}');
  // created by us: deleted
  const d2 = path.join(base, 'p2'); mkdirSync(d2);
  run('browser-opencode.sh', [d2], e);
  run('browser-opencode.sh', ['--remove', d2], e);
  assert.equal(existsSync(path.join(d2, 'opencode.json')), false);
  // mode of a user's file survives an add/remove cycle
  const d3 = path.join(base, 'p3'); mkdirSync(d3);
  writeFileSync(path.join(d3, 'opencode.json'), '{"a":1}'); chmodSync(path.join(d3, 'opencode.json'), 0o640);
  run('browser-opencode.sh', [d3], e); run('browser-opencode.sh', ['--remove', d3], e);
  assert.equal(lstatSync(path.join(d3, 'opencode.json')).mode & 0o777, 0o640);
  rmSync(base, { recursive: true, force: true });
});

test('install-opencode.sh: unparsable config or string command -> skill link still removed, exit reflects failure', () => {
  const { base, skill, e } = fixture();
  const st = stubs(base);
  const home = path.join(base, 'oc'); mkdirSync(path.join(home, 'skills'), { recursive: true });
  symlinkSync(skill, path.join(home, 'skills/chrome-devtools'));
  writeFileSync(path.join(home, 'opencode.json'), '// jsonc\n{"mcp":{}}');
  const r = run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: st.PATH });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /left unchanged\. jq said: jq: parse error/);
  assert.throws(() => lstatSync(path.join(home, 'skills/chrome-devtools')), 'skill link removed before the config step');
  assert.equal(readFileSync(path.join(home, 'opencode.json'), 'utf8'), '// jsonc\n{"mcp":{}}');
  assert.deepEqual(execFileSync('ls', ['-A', home], { encoding: 'utf8' }).trim().split('\n').sort(), ['opencode.json', 'skills']);
  // a string-typed command is neither ours nor a crash
  const cfg = { mcp: { 'chrome-devtools': { type: 'local', command: '/bin/other' } } };
  writeFileSync(path.join(home, 'opencode.json'), JSON.stringify(cfg));
  assert.equal(run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: st.PATH }).status, 0);
  assert.deepEqual(readJson(path.join(home, 'opencode.json')), cfg);
  rmSync(base, { recursive: true, force: true });
});

test('exclude lines escape gitignore specials ([ ] \\) and stay anchored', () => {
  const { base, e } = fixture();
  const d = repo(base, 'main');
  const odd = path.join(d, 'a[b]\\c'), lookalike = path.join(d, 'ab'), lookalike2 = path.join(d, 'a[b]');
  for (const x of [odd, lookalike, lookalike2]) mkdirSync(x);
  writeFileSync(path.join(lookalike, 'opencode.json'), '{}');   // must NOT be matched by an unescaped [b]
  assert.equal(run('browser-opencode.sh', [odd], e).status, 0);
  const st = git(d, 'status', '--porcelain', '-uall');
  assert.match(st, /ab\/opencode\.json/, 'lookalike dir stays visible');
  assert.doesNotMatch(st, /a\[b\]/, 'our files in the odd dir are hidden');
  assert.equal(run('browser-opencode.sh', ['--remove', odd], e).status, 0);
  assert.doesNotMatch(git(d, 'status', '--porcelain', '-uall'), /a\[b\]/);
  rmSync(base, { recursive: true, force: true });
});

test('user\'s own exclude lines (e.g. /sub/opencode.json) survive the README cleanup snippet', () => {
  const { base, e } = fixture();
  const d = repo(base, 'main');
  const ex = path.join(d, '.git/info/exclude');
  const mine = '/sub/opencode.json\n/opencode.json.bak\n';
  writeFileSync(ex, mine);
  run('browser-opencode.sh', [d], e);
  execFileSync('bash', ['-c', CLEANUP], { cwd: d });
  assert.equal(readFileSync(ex, 'utf8'), mine);
  rmSync(base, { recursive: true, force: true });
});

test('symlinked config (dotfiles): add / --remove / install keep the symlink, update the target, keep its mode', () => {
  const { base, e } = fixture();
  const dot = path.join(base, 'dotfiles'); mkdirSync(dot);
  const real = path.join(dot, 'opencode.json');
  const orig = { update: 'disable', mcp: { other: { type: 'remote', url: 'http://127.0.0.1:1/x' } } };
  const isLink = (f) => lstatSync(f).isSymbolicLink();
  const mode = (f) => lstatSync(f).mode & 0o777;

  for (const target of [real, path.relative(path.join(base, 'proj'), real)]) {   // absolute and relative link
    const d = path.join(base, 'proj'); mkdirSync(d, { recursive: true });
    writeFileSync(real, JSON.stringify(orig)); chmodSync(real, 0o640);
    symlinkSync(target, path.join(d, 'opencode.json'));
    assert.equal(run('browser-opencode.sh', [d], e).status, 0);
    assert.ok(isLink(path.join(d, 'opencode.json')), 'still a symlink after add');
    assert.equal(readlinkSync(path.join(d, 'opencode.json')), target);
    assert.deepEqual(readJson(real), { ...orig, mcp: { ...orig.mcp, 'chrome-devtools': ours } });
    assert.equal(mode(real), 0o640);
    assert.equal(run('browser-opencode.sh', ['--remove', d], e).status, 0);
    assert.ok(isLink(path.join(d, 'opencode.json')), 'still a symlink after --remove');
    assert.deepEqual(readJson(real), orig);
    assert.equal(mode(real), 0o640);
    assert.deepEqual(execFileSync('ls', ['-A', dot], { encoding: 'utf8' }).trim().split('\n'), ['opencode.json'], 'no stray temp files');
    rmSync(d, { recursive: true });
  }

  const st = stubs(base);
  const home = path.join(base, 'oc'); mkdirSync(home);
  writeFileSync(real, JSON.stringify({ ...orig, mcp: { 'cdp-enh': {}, ...orig.mcp } })); chmodSync(real, 0o640);
  symlinkSync(real, path.join(home, 'opencode.json'));
  assert.equal(run('install-opencode.sh', [], { ...e, OPENCODE_HOME: home, PATH: st.PATH }).status, 0);
  assert.ok(isLink(path.join(home, 'opencode.json')));
  assert.deepEqual(readJson(real), orig);
  assert.equal(mode(real), 0o640);
  rmSync(base, { recursive: true, force: true });
});
