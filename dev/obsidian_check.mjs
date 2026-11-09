#!/usr/bin/env node
// Render tracker views in a real, isolated Obsidian and read them back.
//
// A regression check for the .base files: it starts a second Obsidian with its
// own settings folder, opens a throwaway vault, opens each view, prints the
// table that Obsidian actually rendered, and saves a screenshot.
//
// It never touches your own Obsidian:
//   * --data-dir is required and must not be Obsidian's normal settings folder.
//   * --vault must be a demo vault made by dev/make_demo_vault.py (it carries a
//     .workdecero-scratch marker file) and must not be one of your own vaults.
//   * Your vault list is compared before and after, and the run fails if it changed.
//
// Usage:
//   node dev/obsidian_check.mjs --vault DIR --data-dir DIR --out DIR [options] TARGET...
//     TARGET        a note or a view, for example "workdecero/Jobs.base#Pipeline"
//     --port N      debug port (default: a free port, so two checks can run side by side)
//     --width N     window width in pixels (default 1280)
//     --height N    window height in pixels (default 800)
//     --obsidian P  path to the Obsidian program (default: the usual place for this system)
//     --collapse-sidebar   hide the file list before rendering
//
// Needs Node 22 or newer and Obsidian installed. Nothing else.

import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const MARKER = '.workdecero-scratch';

function fail(message) {
  console.error('obsidian check: ' + message);
  process.exit(2);
}

function parseArgs(argv) {
  const options = { port: 0, width: 1280, height: 800, targets: [], collapse: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--collapse-sidebar') options.collapse = true;
    else if (arg.startsWith('--')) {
      const key = { '--vault': 'vault', '--data-dir': 'dataDir', '--out': 'out', '--port': 'port', '--width': 'width',
        '--height': 'height', '--obsidian': 'obsidian' }[arg];
      if (!key || i + 1 >= argv.length) fail('unknown or incomplete option ' + arg);
      options[key] = argv[++i];
    } else options.targets.push(arg);
  }
  for (const key of ['port', 'width', 'height']) options[key] = Number(options[key]);
  return options;
}

function defaultSettingsDirs() {
  const home = os.homedir();
  return [
    path.join(home, 'Library', 'Application Support', 'obsidian'),
    path.join(home, '.config', 'obsidian'),
    path.join(process.env.APPDATA || path.join(home, 'AppData', 'Roaming'), 'obsidian'),
  ];
}

function defaultProgram() {
  if (process.platform === 'darwin') return '/Applications/Obsidian.app/Contents/MacOS/Obsidian';
  if (process.platform === 'win32') return path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Obsidian', 'Obsidian.exe');
  return 'obsidian';
}

function inside(child, parent) {
  const relative = path.relative(parent, child);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function registryState() {
  // The user's own vault list: its hash, and the vault paths in it. Read only.
  const state = { hash: [], vaults: [] };
  for (const dir of defaultSettingsDirs()) {
    const file = path.join(dir, 'obsidian.json');
    if (!fs.existsSync(file)) continue;
    const text = fs.readFileSync(file);
    state.hash.push(crypto.createHash('sha256').update(text).digest('hex'));
    try {
      for (const vault of Object.values(JSON.parse(text).vaults || {})) state.vaults.push(fs.realpathSync(vault.path));
    } catch { /* unreadable list: nothing to compare paths against */ }
  }
  return state;
}

const options = parseArgs(process.argv.slice(2));
if (!options.vault || !options.dataDir || !options.out || !options.targets.length) {
  fail('--vault, --data-dir, --out and at least one target are required. See the top of this file.');
}
if (!fs.existsSync(options.vault)) fail('the vault folder does not exist: ' + options.vault);
const vault = fs.realpathSync(options.vault);
// Check the settings folder before creating anything, so a refused run leaves no trace.
const wantedDataDir = path.resolve(options.dataDir);
for (const dir of defaultSettingsDirs()) {
  for (const real of new Set([dir, fs.existsSync(dir) ? fs.realpathSync(dir) : dir])) {
    if (inside(wantedDataDir, real) || inside(real, wantedDataDir)) {
      fail('--data-dir must be a separate throwaway folder, not Obsidian\'s own settings folder.');
    }
  }
}
fs.mkdirSync(wantedDataDir, { recursive: true });
const dataDir = fs.realpathSync(wantedDataDir);
if (!fs.existsSync(path.join(vault, MARKER))) {
  fail('refusing to open ' + vault + ': it has no ' + MARKER + ' file. Only demo vaults made by dev/make_demo_vault.py are opened.');
}
const before = registryState();
if (before.vaults.some(own => inside(vault, own) || inside(own, vault))) {
  fail('refusing to open ' + vault + ': it is, or sits inside, one of your own Obsidian vaults.');
}
if (inside(dataDir, vault)) fail('--data-dir must not be inside the vault.');

if (!options.port) {
  // Another isolated Obsidian may already be listening on a fixed port. Ask the system for a free one.
  options.port = await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
  });
}

fs.writeFileSync(path.join(dataDir, 'obsidian.json'),
  JSON.stringify({ vaults: { aaaaaaaaaaaaaaaa: { path: vault, ts: Date.now(), open: true } } }));

const child = spawn(options.obsidian || defaultProgram(),
  ['--user-data-dir=' + dataDir, '--remote-debugging-port=' + options.port], { stdio: 'ignore' });
child.on('error', error => fail('could not start Obsidian: ' + error.message));

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let exitCode = 0;
try {
  let page = null;
  for (let attempt = 0; attempt < 40 && !page; attempt++) {
    await sleep(500);
    try {
      const list = await (await fetch(`http://127.0.0.1:${options.port}/json/list`)).json();
      page = list.find(target => target.type === 'page' && target.url.startsWith('app://'));
    } catch { /* not up yet */ }
  }
  if (!page) throw new Error('Obsidian did not open a window in time.');
  await sleep(2500);

  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve); socket.addEventListener('error', reject); });
  let nextId = 0;
  const waiting = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && waiting.has(message.id)) { waiting.get(message.id)(message); waiting.delete(message.id); }
  });
  const send = (method, params = {}) => new Promise(resolve => {
    const id = ++nextId; waiting.set(id, resolve); socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const reply = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (reply.result.exceptionDetails) throw new Error(reply.result.exceptionDetails.exception?.description || 'script error');
    return reply.result.result.value;
  };

  const opened = await evaluate('app.vault.adapter.basePath');
  if (fs.realpathSync(opened) !== vault) throw new Error('Obsidian opened a different vault than the one given (' + opened + '). Stopping.');

  await send('Emulation.setDeviceMetricsOverride',
    { width: options.width, height: options.height, deviceScaleFactor: 1, mobile: false });
  if (options.collapse) await evaluate('app.workspace.leftSplit.collapse()');
  fs.mkdirSync(options.out, { recursive: true });

  const report = [];
  for (const target of options.targets) {
    await evaluate(`app.workspace.openLinkText(${JSON.stringify(target)}, '', false).then(() => 'ok')`);
    await sleep(1800);
    const view = await evaluate(`(() => {
      const el = app.workspace.activeLeaf.view.containerEl;
      const cellText = c => { const box = c.querySelector('input[type=checkbox]');
        return (box ? (box.checked ? '[x]' : '[ ]') : (c.innerText || c.querySelector('input')?.value || '')).trim().replace(/\\n/g, ' | '); };
      const headers = [...el.querySelectorAll('.bases-thead .bases-td')].map(c => c.innerText.trim());
      const rows = [];
      for (const node of el.querySelectorAll('.bases-group-heading, .bases-tbody .bases-tr')) {
        if (node.classList.contains('bases-group-heading')) rows.push({ group: node.innerText.replace(/\\n/g, ' ') });
        else rows.push({ cells: [...node.querySelectorAll('.bases-td')].map(cellText) });
      }
      const errors = [...el.querySelectorAll('.bases-error, .mod-error')].map(e => e.innerText.trim()).filter(Boolean);
      return { type: app.workspace.activeLeaf.view.getViewType(), headers, rows, errors };
    })()`);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    const file = path.join(options.out, target.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '') + '.png');
    if (shot.result?.data) fs.writeFileSync(file, Buffer.from(shot.result.data, 'base64'));
    report.push({ target, ...view, screenshot: file });
    if (view.errors.length) exitCode = 1;
  }
  console.log(JSON.stringify(report, null, 1));
  socket.close();
} catch (error) {
  console.error('obsidian check: ' + error.message);
  exitCode = 2;
} finally {
  child.kill();
  await sleep(800);
}

const after = registryState();
if (JSON.stringify(after.hash) !== JSON.stringify(before.hash)) {
  console.error('obsidian check: your own Obsidian vault list changed during the run. Look at it before running again.');
  exitCode = 3;
}
process.exit(exitCode);
