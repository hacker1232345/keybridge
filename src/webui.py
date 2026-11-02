#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webui.py — keybridge 内置 Web 控制台

在浏览器里增删改热键，点「保存」直接写回 config.json 并立即热重载生效。
不需要重开 keybridge，不需要手动编辑 JSON。

安全设计：
  * 只绑定 127.0.0.1（不暴露到局域网）
  * 每个进程随机生成一次性 token，/api/* 都要带 ?t=<token>（本机回环地址例外，方便直接用）
  * 写文件前先备份成 config.json.bak，再原子替换
  * 「试一下」动作复用 keybridge 的 ActionRunner，能力边界与热键完全一致

这个模块不自己起服务器，由 keybridge.py 的 HTTP 服务把请求路由进来。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
import urllib.parse

# ---------------------------------------------------------------- 键名清单

# 浏览器 KeyboardEvent.code → keybridge 键名。
# 用于网页上的「捕获」按钮：点一下，然后按键盘上的键，自动填进去。
CODE_TO_KEY: dict[str, str] = {}


def _init_code_map() -> None:
    m = {
        "Escape": "ESC", "Backquote": "GRAVE", "Minus": "MINUS", "Equal": "EQUAL",
        "Backspace": "BACKSPACE", "Tab": "TAB",
        "BracketLeft": "LBRACKET", "BracketRight": "RBRACKET", "Backslash": "BACKSLASH",
        "CapsLock": "CAPSLOCK", "Semicolon": "SEMICOLON", "Quote": "QUOTE", "Enter": "ENTER",
        "Comma": "COMMA", "Period": "PERIOD", "Slash": "SLASH",
        "ControlLeft": "LCTRL", "ControlRight": "RCTRL",
        "ShiftLeft": "LSHIFT", "ShiftRight": "RSHIFT",
        "AltLeft": "LALT", "AltRight": "RALT",
        "Space": "SPACE", "MetaLeft": "LWIN", "MetaRight": "RWIN", "ContextMenu": "APPS",
        "Insert": "INSERT", "Home": "HOME", "PageUp": "PAGEUP",
        "Delete": "DELETE", "End": "END", "PageDown": "PAGEDOWN",
        "ArrowUp": "UP", "ArrowDown": "DOWN", "ArrowLeft": "LEFT", "ArrowRight": "RIGHT",
        "NumLock": "NUMLOCK", "NumpadDivide": "NUMDIV", "NumpadMultiply": "NUMMUL",
        "NumpadSubtract": "NUMSUB", "NumpadAdd": "NUMADD", "NumpadDecimal": "NUMDEC",
        "PrintScreen": "PRTSC", "ScrollLock": "SCROLLLOCK", "Pause": "PAUSE",
        "AudioVolumeUp": "VOLUME_UP", "AudioVolumeDown": "VOLUME_DOWN",
        "AudioVolumeMute": "VOLUME_MUTE",
        "MediaTrackNext": "MEDIA_NEXT", "MediaTrackPrevious": "MEDIA_PREV",
        "MediaStop": "MEDIA_STOP", "MediaPlayPause": "MEDIA_PLAY",
        "BrowserBack": "BROWSER_BACK", "BrowserForward": "BROWSER_FORWARD",
        "BrowserRefresh": "BROWSER_REFRESH", "BrowserHome": "BROWSER_HOME",
        "BrowserSearch": "BROWSER_SEARCH", "BrowserFavorites": "BROWSER_FAVORITES",
        "LaunchMail": "LAUNCH_MAIL", "LaunchMediaPlayer": "LAUNCH_MEDIA",
        "LaunchApp1": "LAUNCH_APP1", "LaunchApp2": "LAUNCH_APP2",
    }
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        m["Key" + c] = c
    for d in "0123456789":
        m["Digit" + d] = d
        m["Numpad" + d] = "NUM" + d
    for i in range(1, 25):
        m["F%d" % i] = "F%d" % i
    CODE_TO_KEY.update(m)


_init_code_map()


# ---------------------------------------------------------------- 页面
#
# 注意：这是个 Python 原始字符串，里面的 ' 和 \ 都是字面量。
# 生成 HTML 属性里的 JS 时一律用模板字符串（反引号）拼，避免引号转义地狱。

PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>keybridge 控制台</title>
<style>
:root{
  --bg:#0e1117; --panel:#161b22; --panel2:#1c2230; --line:#2b3242;
  --fg:#e6edf3; --dim:#8b949e; --accl:#4c8dff; --ok:#3fb950; --warn:#d29922; --err:#f85149;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
a{color:var(--accl);text-decoration:none}
a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:20;background:rgba(14,17,23,.94);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:12px 20px;
  display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.3px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--dim);display:inline-block}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.spacer{flex:1}
button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
  border-radius:7px;padding:7px 13px;cursor:pointer;font-size:13px;transition:.12s;
  font-family:inherit}
button:hover{background:#242c3c;border-color:#3d4658}
button.pri{background:var(--accl);border-color:var(--accl);color:#fff;font-weight:600}
button.pri:hover{background:#3a7de8}
button.danger:hover{border-color:var(--err);color:var(--err)}
button.sm{padding:4px 9px;font-size:12px}
button.gh{background:transparent}
main{max-width:1180px;margin:0 auto;padding:20px 20px 88px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:15px 17px;margin-bottom:13px}
.card.off{opacity:.55}
.card-head{display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin-bottom:12px}
.id{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--dim)}
.keys{display:flex;gap:5px;flex-wrap:wrap;align-items:center}
.k{background:#22304a;border:1px solid #35496e;border-radius:6px;padding:2px 9px;
  font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:#a8c7ff}
.k.rec{background:#4a2b22;border-color:#6e4935;color:#ffc9a8;animation:bl 1s infinite}
@keyframes bl{50%{opacity:.45}}
.row{display:flex;gap:13px;flex-wrap:wrap;margin-bottom:10px}
.f{display:flex;flex-direction:column;gap:4px;min-width:130px}
.f.grow{flex:1;min-width:250px}
label{font-size:11.5px;color:var(--dim);letter-spacing:.3px}
input,select,textarea{background:#0d1117;color:var(--fg);border:1px solid var(--line);
  border-radius:7px;padding:7px 10px;font-size:13px;font-family:inherit;width:100%}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accl)}
textarea{resize:vertical;min-height:64px;font-family:ui-monospace,Consolas,monospace;
  font-size:12.5px;line-height:1.5}
.chk{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--dim);
  cursor:pointer;user-select:none;white-space:nowrap}
.chk input{width:auto;margin:0;cursor:pointer}
.hint{font-size:11.5px;color:var(--dim);margin-top:3px}
.bar{position:fixed;left:0;right:0;bottom:0;background:rgba(22,27,34,.97);
  backdrop-filter:blur(8px);border-top:1px solid var(--line);padding:11px 20px;
  display:flex;align-items:center;gap:13px;z-index:30}
.toast{position:fixed;right:18px;bottom:76px;background:var(--panel2);
  border:1px solid var(--line);border-left:3px solid var(--ok);border-radius:8px;
  padding:11px 15px;font-size:13px;z-index:50;opacity:0;transform:translateY(8px);
  transition:.2s;pointer-events:none;max-width:420px;white-space:pre-wrap}
.toast.show{opacity:1;transform:none}
.toast.err{border-left-color:var(--err)}
pre#log{background:#0a0d12;border:1px solid var(--line);border-radius:9px;
  padding:13px;max-height:60vh;overflow:auto;font-size:12px;
  font-family:ui-monospace,Consolas,monospace;line-height:1.6;margin:0;
  white-space:pre-wrap;word-break:break-all}
.tabs{display:flex;gap:5px;margin-bottom:15px;border-bottom:1px solid var(--line)}
.tab{padding:8px 15px;cursor:pointer;color:var(--dim);border-bottom:2px solid transparent;
  font-size:13.5px}
.tab.on{color:var(--fg);border-bottom-color:var(--accl)}
.hidden{display:none!important}
.empty{text-align:center;color:var(--dim);padding:44px 0}
.warn{border-left:3px solid var(--warn);background:#241f14;border-radius:7px;
  padding:9px 13px;font-size:12.5px;color:#e3c88a;margin-bottom:11px}
</style>
</head>
<body>
<header>
  <h1>keybridge 控制台</h1>
  <span class="dot" id="dot"></span>
  <span id="status" style="font-size:12.5px;color:var(--dim)">连接中…</span>
  <span class="spacer"></span>
  <span id="path" style="font-size:11.5px;color:var(--dim)"></span>
  <button class="gh sm" id="btnLog">看日志</button>
  <button class="gh sm" id="btnReload">重新加载</button>
</header>

<main>
  <div class="tabs">
    <div class="tab on" data-tab="hot">热键</div>
    <div class="tab" data-tab="log">日志</div>
  </div>

  <div id="tab-hot">
    <div class="warn hidden" id="tokenWarn"></div>
    <div id="list"></div>
    <button id="btnAdd" style="width:100%;padding:12px">+ 新增一条热键</button>
  </div>

  <div id="tab-log" class="hidden">
    <div class="card">
      <div class="card-head">
        <strong>运行日志</strong>
        <span class="spacer"></span>
        <label class="chk"><input type="checkbox" id="autoLog" checked> 自动刷新</label>
        <button class="sm" id="btnLog2">刷新</button>
      </div>
      <pre id="log">（加载中…）</pre>
    </div>
  </div>
</main>

<div class="bar">
  <span id="dirty" style="font-size:12.5px;color:var(--dim)">没有未保存的修改</span>
  <span class="spacer"></span>
  <span id="count" style="font-size:12.5px;color:var(--dim)"></span>
  <button id="btnDiscard">放弃修改</button>
  <button class="pri" id="btnSave">保存并生效</button>
</div>

<div class="toast" id="toast"></div>

<script>
const CODE_TO_KEY = __CODE_TO_KEY__;

let T = new URLSearchParams(location.search).get('t') || '';
if (T) { try { localStorage.setItem('kbToken', T); } catch(e){} }
else { try { T = localStorage.getItem('kbToken') || ''; } catch(e){ T = ''; } }

const ACTION_TYPES = [
  ['run','run · 启动程序'],
  ['shell','shell · 执行命令'],
  ['code','code · 内联代码'],
  ['script','script · 脚本文件'],
  ['url','url · 打开网址/文件'],
  ['opendir','opendir · 打开目录'],
  ['keys','keys · 模拟按键'],
  ['sequence','sequence · 多步骤'],
  ['http','http · 发 HTTP 请求'],
];
const TRIGGERS = [['down','按下触发'],['up','抬起触发'],['long','长按触发']];
const SHELLS = [['cmd','cmd'],['powershell','powershell'],['pwsh','pwsh'],['bash','bash']];
const LANGS = [['python','python'],['powershell','powershell'],['bat','bat'],['cmd','cmd'],['js','js / node'],['vbs','vbs']];

let CFG = null;
let PRISTINE = '';
let KEYS = [];
let rec = null;
let TOKEN_OK = true;

function $(id){ return document.getElementById(id); }

function api(path, opts){
  const sep = path.includes('?') ? '&' : '?';
  const url = T ? (path + sep + 't=' + encodeURIComponent(T)) : path;
  return fetch(url, opts).then(function(r){
    return r.json().then(function(j){ return {ok:r.ok, j:j, status:r.status}; })
                   .catch(function(){ return {ok:false, j:{error:'返回不是 JSON (HTTP '+r.status+')'}, status:r.status}; });
  }).catch(function(e){ return {ok:false, j:{error:'连不上服务: '+e.message}, status:0}; });
}

function toast(msg, err){
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(el._t);
  el._t = setTimeout(function(){ el.className = 'toast' + (err ? ' err' : ''); }, err ? 8000 : 2600);
}
function esc(s){
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function attr(s){ return esc(s); }

// ---------------- 加载 ----------------
async function loadState(){
  const r = await api('/api/state');
  if(!r.ok){
    $('dot').className = 'dot';
    $('status').textContent = '连不上（' + (r.j.error || ('HTTP ' + r.status)) + '）';
    $('tokenWarn').className = 'warn';
    $('tokenWarn').textContent = '读取配置失败：' + (r.j.error||'')
      + '\n请用启动日志里带 ?t=<token> 的完整地址打开本页面。';
    return;
  }
  TOKEN_OK = true;
  $('tokenWarn').className = 'warn hidden';
  CFG = r.j.config;
  if(!Array.isArray(CFG.hotkeys)) CFG.hotkeys = [];
  KEYS = r.j.keyNames || [];
  $('path').textContent = r.j.configPath;
  $('status').textContent = '运行中 · 已生效 ' + r.j.activeCount + ' 条'
    + (r.j.injected ? ' · 响应注入键' : '');
  $('dot').className = 'dot on';
  PRISTINE = snap();
  render();
  fillDatalist();
}
function snap(){ return JSON.stringify((CFG && CFG.hotkeys) || []); }
function checkDirty(){
  const d = snap() !== PRISTINE;
  const el = $('dirty');
  el.textContent = d ? '⚠ 有未保存的修改' : '没有未保存的修改';
  el.style.color = d ? 'var(--warn)' : 'var(--dim)';
  $('count').textContent = '共 ' + ((CFG && CFG.hotkeys) || []).length + ' 条';
}
function fillDatalist(){
  let dl = $('keydl');
  if(!dl){ dl = document.createElement('datalist'); dl.id = 'keydl'; document.body.appendChild(dl); }
  dl.innerHTML = KEYS.map(function(k){ return '<option value="' + attr(k) + '">'; }).join('');
}

// ---------------- 渲染 ----------------
function render(){
  const box = $('list');
  if(!CFG.hotkeys.length){
    box.innerHTML = '<div class="empty">还没有热键。点下面的按钮加一条。</div>';
    checkDirty(); return;
  }
  box.innerHTML = CFG.hotkeys.map(function(h,i){ return cardHTML(h,i); }).join('');
  checkDirty();
}

function cardHTML(h, i){
  const a = h.action || (h.action = {type:'run'});
  if(!a.type) a.type = 'run';
  const on = h.enabled !== false;

  const keyChips = (h.keys || []).map(function(k){
    return '<span class="k">' + esc(k) + '</span>';
  }).join('');

  return `<div class="card${on ? '' : ' off'}" data-i="${i}">
  <div class="card-head">
    <input type="checkbox" ${on ? 'checked' : ''} onchange="setField(${i},'enabled',this.checked)" title="启用 / 停用">
    <b>${esc(h.name || '(未命名)')}</b>
    <span class="id">${esc(h.id || '')}</span>
    <span class="spacer"></span>
    <button class="sm" onclick="testOne(${i})">试一下</button>
    <button class="sm danger" onclick="delCard(${i})">删除</button>
  </div>

  <div class="row">
    <div class="f grow"><label>ID（唯一标识）</label>
      <input value="${attr(h.id || '')}" oninput="setField(${i},'id',this.value)" placeholder="my-key"></div>
    <div class="f grow"><label>名称</label>
      <input value="${attr(h.name || '')}" oninput="setField(${i},'name',this.value)" placeholder="给这条起个名字"></div>
  </div>

  <div class="row">
    <div class="f grow"><label>触发按键</label>
      <div class="keys">
        ${keyChips}
        <span class="k${rec === i ? ' rec' : ''}" onclick="startRec(${i})" style="cursor:pointer">${rec === i ? '请按键…' : '+ 捕获'}</span>
      </div>
      <div class="hint">
        <a href="#" onclick="clearKeys(${i});return false">清空</a> ·
        <a href="#" onclick="addKeyManual(${i});return false">手动输入</a> ·
        <a href="#" onclick="presetKeys(${i});return false">常用组合</a> ·
        多个键 = 组合键（顺序无关）
      </div>
    </div>
  </div>

  <div class="row">
    <div class="f"><label>触发方式</label>${selHTML(TRIGGERS, h.trigger || 'down', `setField(${i},'trigger',this.value)`)}</div>
    <div class="f"><label>长按阈值 (ms)</label>
      <input type="number" value="${Number(h.longpress_ms) || 600}" oninput="setField(${i},'longpress_ms',+this.value)"></div>
    <div class="f"><label>选项</label>
      <div style="display:flex;gap:14px;padding-top:7px;flex-wrap:wrap">
        <label class="chk"><input type="checkbox" ${h.swallow ? 'checked' : ''} onchange="setField(${i},'swallow',this.checked)"> 吞掉这个键</label>
        <label class="chk"><input type="checkbox" ${h.repeat ? 'checked' : ''} onchange="setField(${i},'repeat',this.checked)"> 按住连发</label>
      </div>
    </div>
  </div>

  <div class="row">
    <div class="f"><label>动作类型</label>${selHTML(ACTION_TYPES, a.type, `setType(${i},this.value)`)}</div>
    <div class="f grow" id="af${i}">${actionFields(a, i)}</div>
  </div>
</div>`;
}

function selHTML(pairs, cur, onchange){
  return `<select onchange="${onchange}">` + pairs.map(function(p){
    return `<option value="${attr(p[0])}"${String(p[0]) === String(cur) ? ' selected' : ''}>${esc(p[1])}</option>`;
  }).join('') + '</select>';
}
function iField(label, path, a, i, grow){
  const v = path.split('.').reduce(function(o,k){ return o && o[k]; }, a);
  return `<div class="f${grow ? ' grow' : ''}"><label>${label}</label>
    <input value="${attr(v === null || v === undefined ? '' : v)}" oninput="setAct(${i},'${path}',this.value)"></div>`;
}
function iNum(label, path, a, i){
  const v = path.split('.').reduce(function(o,k){ return o && o[k]; }, a);
  return `<div class="f"><label>${label}</label>
    <input type="number" value="${attr(v === null || v === undefined ? '' : v)}" oninput="setAct(${i},'${path}',+this.value)"></div>`;
}
function iArea(label, path, a, i, ph, h){
  const v = path.split('.').reduce(function(o,k){ return o && o[k]; }, a);
  return `<div class="f grow"><label>${label}</label>
    <textarea style="min-height:${h || 64}px" placeholder="${attr(ph || '')}" oninput="setAct(${i},'${path}',this.value)">${esc(v || '')}</textarea></div>`;
}
function cBox(label, path, a, i){
  const v = !!path.split('.').reduce(function(o,k){ return o && o[k]; }, a);
  return `<label class="chk"><input type="checkbox" ${v ? 'checked' : ''} onchange="setAct(${i},'${path}',this.checked)"> ${label}</label>`;
}
function argsField(a, i){
  const v = (a.args || []).join(' ');
  return `<div class="f grow"><label>参数（空格分隔）</label>
    <input value="${attr(v)}" oninput="setAct(${i},'args',this.value.split(/\s+/).filter(Boolean))"></div>`;
}

function actionFields(a, i){
  const t = a.type || 'run';
  let h = '';
  if(t === 'run'){
    h += `<div class="row">${iField('程序（可执行文件或 .lnk 快捷方式；也可只写 notepad.exe）','program',a,i,true)}</div>`;
    h += `<div class="hint">支持 <b>exe / .lnk 快捷方式 / .bat / .url / 文档</b>；还有 <b>网址键</b> 可以直接填 http 链接。路径可以直接从桌面复制过来。</div>`;
    h += `<div class="row">${argsField(a,i)}${iField('工作目录','cwd',a,i,true)}</div>`;
    h += `<div style="display:flex;gap:14px">${cBox('隐藏窗口','hidden',a,i)}${cBox('显示控制台','console',a,i)}</div>`;
  } else if(t === 'shell'){
    h += `<div class="row"><div class="f"><label>Shell</label>${selHTML(SHELLS, a.shell || 'cmd', `setAct(${i},'shell',this.value)`)}</div>`
       + `${iField('工作目录','cwd',a,i,true)}</div>`;
    h += `<div class="row">${iArea('命令','command',a,i,'echo hi >> D:\\out.log',70)}</div>`;
    h += `<div style="display:flex;gap:14px">${cBox('隐藏窗口','hidden',a,i)}${cBox('显示控制台','console',a,i)}</div>`;
  } else if(t === 'code'){
    h += `<div class="row"><div class="f"><label>语言</label>${selHTML(LANGS, a.lang || 'python', `setAct(${i},'lang',this.value)`)}</div></div>`;
    h += `<div class="row">${iArea('代码（落盘到 runtime/tmp 后执行）','code',a,i,
            'import datetime,pathlib' + String.fromCharCode(10) + 'pathlib.Path(r"D:/out.log").open("a",encoding="utf-8").write(str(datetime.datetime.now()))', 110)}</div>`;
  } else if(t === 'script'){
    h += `<div class="row">${iField('脚本文件','file',a,i,true)}${iField('解释器（留空按扩展名）','interpreter',a,i)}</div>`;
    h += `<div class="row">${argsField(a,i)}${iField('工作目录','cwd',a,i,true)}</div>`;
    h += `<div style="display:flex;gap:14px">${cBox('隐藏窗口','hidden',a,i)}${cBox('显示控制台','console',a,i)}</div>`;
  } else if(t === 'url'){
    h += `<div class="row">${iField('网址或文件路径','url',a,i,true)}</div>`;
  } else if(t === 'opendir'){
    h += `<div class="row">${iField('目录','dir',a,i,true)}</div>`;
  } else if(t === 'keys'){
    h += `<div class="row">${iField('要按的键（+ 组合，{名字} 花括号）','keys',a,i,true)}${iNum('间隔秒','delay',a,i)}</div>`;
  } else if(t === 'http'){
    h += `<div class="row">${iField('URL','url',a,i,true)}`
       + `<div class="f"><label>方法</label>${selHTML([['GET','GET'],['POST','POST'],['PUT','PUT']], a.method || 'GET', `setAct(${i},'method',this.value)`)}</div></div>`;
    h += `<div class="row">${iArea('Body','body',a,i,'',64)}</div>`;
  } else if(t === 'sequence'){
    h += `<div class="row"><div class="f grow"><label>步骤（JSON 数组）</label>
      <textarea style="min-height:120px" oninput="setSeq(${i},this)">${esc(JSON.stringify(a.steps || [], null, 1))}</textarea>
      <div class="hint">每步是一个动作对象，可加 "waitAfter": 1.5 表示执行后等 1.5 秒</div></div></div>`;
  }
  h += `<div class="hint" style="margin-top:8px"><a href="#" onclick="toggleRaw(${i});return false">高级：直接编辑这条动作的 JSON</a></div>`;
  h += `<div class="hidden" id="raw${i}" style="margin-top:8px">
    <textarea style="min-height:130px" oninput="setRaw(${i},this)">${esc(JSON.stringify(a, null, 2))}</textarea></div>`;
  return h;
}

// ---------------- 字段编辑 ----------------
function setField(i, k, v){
  CFG.hotkeys[i][k] = v;
  if(k === 'id' || k === 'name') renderSoft(i);
  if(k === 'enabled') render();
  checkDirty();
}
function renderSoft(i){
  const card = document.querySelector('.card[data-i="' + i + '"]');
  if(!card) return;
  card.querySelector('b').textContent = CFG.hotkeys[i].name || '(未命名)';
  card.querySelector('.id').textContent = CFG.hotkeys[i].id || '';
}
function setAct(i, path, v){
  const h = CFG.hotkeys[i];
  if(!h.action || typeof h.action !== 'object') h.action = {};
  const parts = path.split('.');
  let o = h.action;
  for(let x = 0; x < parts.length - 1; x++){
    if(typeof o[parts[x]] !== 'object' || o[parts[x]] === null) o[parts[x]] = {};
    o = o[parts[x]];
  }
  o[parts[parts.length - 1]] = v;
  checkDirty();
}
function setRaw(i, el){
  try{
    const o = JSON.parse(el.value);
    CFG.hotkeys[i].action = o;
    el.style.borderColor = '';
    $('af' + i).innerHTML = actionFields(o, i);
    checkDirty();
  }catch(e){ el.style.borderColor = 'var(--err)'; }
}
function setSeq(i, el){
  try{ setAct(i, 'steps', JSON.parse(el.value)); el.style.borderColor = ''; }
  catch(e){ el.style.borderColor = 'var(--err)'; }
}
function toggleRaw(i){ $('raw' + i).classList.toggle('hidden'); }
function setType(i, t){
  const h = CFG.hotkeys[i];
  h.action = Object.assign({}, h.action, {type: t});
  $('af' + i).innerHTML = actionFields(h.action, i);
  checkDirty();
}

// ---------------- 按键捕获 ----------------
function startRec(i){
  if(rec === i){ rec = null; render(); return; }
  rec = i;
  render();
  toast('现在按键盘上你想用的那个键（想取消就按 Esc）');
}
document.addEventListener('keydown', function(e){
  if(rec === null) return;
  e.preventDefault(); e.stopPropagation();
  if(e.code === 'Escape' && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey){
    rec = null; render(); return;
  }
  const name = CODE_TO_KEY[e.code];
  if(!name){ toast('认不出这个键：' + e.code, true); return; }
  const h = CFG.hotkeys[rec];
  if(!Array.isArray(h.keys)) h.keys = [];
  if(h.keys.indexOf(name) < 0) h.keys.push(name);
  rec = null;
  render();
  checkDirty();
}, true);

function clearKeys(i){ CFG.hotkeys[i].keys = []; render(); checkDirty(); }
function addKeyManual(i){
  const s = prompt('输入键名（多个用 + 分隔）：\n\n常用：CTRL  ALT  SHIFT  LWIN  F13  PRTSC  SCROLLLOCK  INSERT  PAUSE\n组合：CTRL+ALT+K\n也支持 VK_7C 或 0x7C 这种写法。',
                    (CFG.hotkeys[i].keys || []).join('+'));
  if(s === null) return;
  const arr = s.split('+').map(function(x){ return x.trim().toUpperCase(); }).filter(Boolean);
  if(arr.length){ CFG.hotkeys[i].keys = arr; render(); checkDirty(); }
}
function presetKeys(i){
  const presets = ['CTRL+ALT+K','CTRL+SHIFT+F12','LWIN+`','F13','F14','F24','PRTSC','SCROLLLOCK','INSERT','PAUSE','RCTRL'];
  const s = prompt('选一个常用组合，或自己改：\n\n' + presets.map(function(p,n){ return (n+1) + '. ' + p; }).join('\n'), 'F13');
  if(s === null) return;
  let v = s.trim();
  const n = parseInt(v, 10);
  if(String(n) === v && presets[n-1]) v = presets[n-1];
  if(v){ CFG.hotkeys[i].keys = v.split('+').map(function(x){ return x.trim().toUpperCase(); }).filter(Boolean); render(); checkDirty(); }
}

// ---------------- 增删 ----------------
function addCard(){
  let n = 1;
  while(CFG.hotkeys.some(function(h){ return h.id === 'hotkey-' + n; })) n++;
  CFG.hotkeys.push({
    id: 'hotkey-' + n, name: '新热键 ' + n, keys: ['F13'],
    trigger: 'down', swallow: true, repeat: false, longpress_ms: 600,
    action: {type:'run', program:'notepad.exe'}
  });
  render();
  checkDirty();
  window.scrollTo(0, document.body.scrollHeight);
}
function delCard(i){
  const h = CFG.hotkeys[i];
  if(!confirm('确定删除「' + (h.name || h.id) + '」？')) return;
  CFG.hotkeys.splice(i, 1);
  render();
  checkDirty();
}

// ---------------- 保存 / 测试 ----------------
async function save(){
  const ids = {};
  for(let k = 0; k < CFG.hotkeys.length; k++){
    const h = CFG.hotkeys[k];
    if(!h.id || !String(h.id).trim()){ toast('第 ' + (k+1) + ' 条没填 ID', true); return; }
    if(ids[h.id]){ toast('ID 重复：' + h.id, true); return; }
    ids[h.id] = 1;
    if(!h.keys || !h.keys.length){ toast('「' + (h.name || h.id) + '」没设按键', true); return; }
    if(!h.action || !h.action.type){ toast('「' + (h.name || h.id) + '」没配动作', true); return; }
  }
  const r = await api('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(CFG)
  });
  if(!r.ok || !r.j.ok){
    const e = r.j && (r.j.error || (r.j.errors || []).join('\n'));
    toast('保存失败：' + (e || ('HTTP ' + r.status)), true);
    return;
  }
  toast('已保存，' + r.j.activeCount + ' 条热键生效' + (r.j.backup ? '\n原文件已备份为 config.json.bak' : ''));
  if(r.j.warnings && r.j.warnings.length){
    setTimeout(function(){ toast('注意：' + r.j.warnings.join('\n'), true); }, 900);
  }
  PRISTINE = snap();
  checkDirty();
  $('status').textContent = '运行中 · 已生效 ' + r.j.activeCount + ' 条';
}

async function testOne(i){
  const h = CFG.hotkeys[i];
  if(!h.action || !h.action.type){ toast('这条没配动作', true); return; }
  toast('正在执行…');
  const r = await api('/api/test', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(h.action)
  });
  toast(r.ok && r.j.ok ? '已执行「' + (h.name || h.id) + '」的动作，切到日志页看结果'
                       : '执行失败：' + ((r.j && r.j.error) || ('HTTP ' + r.status)), !(r.ok && r.j.ok));
}

async function reloadCfg(){
  const r = await api('/api/reload', {method: 'POST'});
  if(r.ok && r.j.ok){ toast('已从磁盘重新加载'); await loadState(); }
  else toast('重新加载失败：' + ((r.j && r.j.error) || '?'), true);
}

// ---------------- 日志 ----------------
async function loadLog(){
  const r = await api('/api/log?n=300');
  if(!r.ok){ $('log').textContent = '读日志失败：' + ((r.j && r.j.error) || ''); return; }
  const el = $('log');
  el.textContent = r.j.log || '(空)';
  el.scrollTop = el.scrollHeight;
}
setInterval(function(){
  if($('autoLog').checked && !$('tab-log').classList.contains('hidden')) loadLog();
}, 3000);

// ---------------- 事件绑定 ----------------
$('btnLog').onclick = function(){ switchTab('log'); };
$('btnLog2').onclick = loadLog;
$('btnReload').onclick = reloadCfg;
$('btnAdd').onclick = addCard;
$('btnSave').onclick = save;
$('btnDiscard').onclick = function(){
  if(snap() !== PRISTINE && !confirm('放弃所有未保存的修改？')) return;
  loadState();
};
function switchTab(k){
  document.querySelectorAll('.tab').forEach(function(t){
    t.classList.toggle('on', t.dataset.tab === k);
  });
  $('tab-hot').classList.toggle('hidden', k !== 'hot');
  $('tab-log').classList.toggle('hidden', k !== 'log');
  if(k === 'log') loadLog();
}
document.querySelectorAll('.tab').forEach(function(t){
  t.onclick = function(){ switchTab(t.dataset.tab); };
});
window.addEventListener('beforeunload', function(e){
  if(snap() !== PRISTINE){ e.preventDefault(); e.returnValue = ''; }
});
document.addEventListener('keydown', function(e){
  if((e.ctrlKey || e.metaKey) && e.key === 's'){ e.preventDefault(); save(); }
});

loadState();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 服务端

class WebUI:
    """把浏览器请求映射到「读配置 / 写配置 / 跑动作 / 读日志」。

    只负责路由与校验，真正的写盘与热重载由 keybridge 传入的回调完成。
    """

    def __init__(self, cfg_path: str, token: str, get_state, do_reload,
                 do_run_action, log_path_fn, key_names=None, injected=False):
        self.cfg_path = cfg_path
        self.token = token
        self.get_state = get_state            # () -> dict
        self.do_reload = do_reload            # () -> (ok, msg, count, warnings)
        self.do_run_action = do_run_action    # (action) -> None
        self.log_path_fn = log_path_fn        # () -> str
        self._names = list(key_names or [])   # 由 keybridge 注入，避免反向 import
        self._lock = threading.Lock()
        self.page = PAGE_HTML.replace("__CODE_TO_KEY__",
                                      json.dumps(CODE_TO_KEY, ensure_ascii=False))

    # -- 工具 ---------------------------------------------------------

    def _is_local(self, h) -> bool:
        try:
            return str(h.client_address[0]) in ("127.0.0.1", "::1")
        except Exception:                                        # noqa: BLE001
            return False

    def _auth(self, h) -> bool:
        q = urllib.parse.urlparse(h.path).query
        t = urllib.parse.parse_qs(q).get("t", [""])[0]
        if t and secrets.compare_digest(t, self.token):
            return True
        return self._is_local(h)

    def _json(self, h, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h.send_response(code)
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Access-Control-Allow-Origin", "*")
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        h.wfile.write(body)

    def _html(self, h, text: str) -> None:
        body = text.encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        h.wfile.write(body)

    def _read_body(self, h):
        n = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:                                        # noqa: BLE001
            return None

    # -- 主路由 -------------------------------------------------------

    def handle(self, h) -> bool:
        """返回 True 表示已处理（调用方不要再走原有逻辑）。"""
        u = urllib.parse.urlparse(h.path)
        path = u.path.rstrip("/") or "/"
        method = h.command.upper()

        # 页面本身：只对本机开放
        if method == "GET" and path == "/":
            if not self._is_local(h):
                self._json(h, 403, {"ok": False, "error": "只允许本机访问"})
                return True
            self._html(h, self.page)
            return True

        if not path.startswith("/api/"):
            return False

        if not self._auth(h):
            self._json(h, 403, {"ok": False,
                                "error": "token 不对。请用启动日志里带 ?t= 的地址打开。\n"
                                         "（token: %s）" % self.token})
            return True

        try:
            if method == "GET" and path == "/api/state":
                st = self.get_state() or {}
                self._json(h, 200, {
                    "ok": True,
                    "config": st.get("config") or {},
                    "configPath": self.cfg_path,
                    "activeCount": st.get("activeCount", 0),
                    "injected": bool(st.get("injected")),
                    "keyNames": self._names,
                })
                return True

            if method == "GET" and path == "/api/log":
                n = urllib.parse.parse_qs(u.query).get("n", ["300"])[0]
                try:
                    n = max(1, min(5000, int(n)))
                except ValueError:
                    n = 300
                self._json(h, 200, {"ok": True, "log": self._tail(n)})
                return True

            if method == "POST" and path == "/api/config":
                body = self._read_body(h)
                if body is None:
                    self._json(h, 400, {"ok": False, "error": "不是合法 JSON"})
                    return True
                if not isinstance(body, dict) or not isinstance(body.get("hotkeys"), list):
                    self._json(h, 400, {"ok": False, "error": "配置必须是带 hotkeys 数组的对象"})
                    return True
                errs = self._validate(body)
                if errs:
                    self._json(h, 400, {"ok": False, "error": errs[0], "errors": errs})
                    return True
                backup = self._write(body)
                ok, msg, count, warns = self.do_reload()
                if not ok:
                    self._json(h, 500, {"ok": False, "error": "文件已写入，但热重载失败：" + msg})
                    return True
                self._json(h, 200, {"ok": True, "activeCount": count,
                                    "backup": backup, "warnings": warns})
                return True

            if method == "POST" and path == "/api/test":
                body = self._read_body(h)
                if not isinstance(body, dict) or not body.get("type"):
                    self._json(h, 400, {"ok": False, "error": "动作必须是带 type 的对象"})
                    return True
                self.do_run_action(body)
                self._json(h, 200, {"ok": True})
                return True

            if method == "POST" and path == "/api/reload":
                ok, msg, count, warns = self.do_reload()
                self._json(h, 200 if ok else 500,
                           {"ok": ok, "error": "" if ok else msg,
                            "activeCount": count, "warnings": warns})
                return True

            self._json(h, 404, {"ok": False, "error": "no such api"})
            return True

        except Exception as e:                                   # noqa: BLE001
            self._json(h, 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
            return True

    # -- 校验（只抓能提前发现的低级错误，不阻止高级用法） --------------

    def _validate(self, cfg: dict) -> list:
        errs: list = []
        seen: set = set()
        for i, h in enumerate(cfg["hotkeys"], 1):
            tag = "第 %d 条" % i
            if not isinstance(h, dict):
                errs.append("%s 不是对象" % tag)
                continue
            hid = str(h.get("id") or "").strip()
            h["id"] = hid
            if not hid:
                errs.append("%s 缺少 id" % tag)
            elif hid in seen:
                errs.append("id 重复：%s" % hid)
            else:
                seen.add(hid)
            keys = h.get("keys")
            if isinstance(keys, str):
                keys = [k for k in keys.replace("+", " ").split() if k]
            if not keys:
                errs.append("%s（%s）没有设置按键" % (tag, hid or "?"))
            else:
                h["keys"] = [canon_key(str(k)) for k in keys]
            if str(h.get("trigger", "down")).lower() not in ("down", "up", "long"):
                errs.append("%s 的 trigger 只能是 down / up / long" % tag)
            a = h.get("action")
            if not isinstance(a, dict) or not a.get("type"):
                errs.append("%s（%s）没有配置动作" % (tag, hid or "?"))
            try:
                int(h.get("longpress_ms", 600) or 600)
            except (TypeError, ValueError):
                errs.append("%s 的长按阈值不是数字" % tag)
        return errs

    # -- 写盘 ---------------------------------------------------------

    def _write(self, cfg: dict) -> bool:
        with self._lock:
            tmp = self.cfg_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.write("\n")
            made_backup = False
            if os.path.exists(self.cfg_path):
                try:
                    shutil.copy2(self.cfg_path, self.cfg_path + ".bak")
                    made_backup = True
                except Exception:                                # noqa: BLE001
                    pass
            os.replace(tmp, self.cfg_path)
            return made_backup

    def _tail(self, n: int) -> str:
        p = self.log_path_fn()
        if not p or not os.path.exists(p):
            return "(没有日志文件)"
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except Exception as e:                                   # noqa: BLE001
            return "读取日志失败: %s" % e


def canon_key(name: str) -> str:
    """跟 keybridge.canon 一致的最小实现（避免反向 import 造成循环）。"""
    n = str(name).strip().upper().replace(" ", "").replace("-", "")
    alias = {
        "ESCAPE": "ESC", "RETURN": "ENTER", "CONTROL": "CTRL", "MENU": "ALT",
        "WIN": "LWIN", "WINDOWS": "LWIN", "META": "LWIN", "SUPER": "LWIN",
        "PRINTSCREEN": "PRTSC", "PAGE_UP": "PAGEUP", "PAGE_DOWN": "PAGEDOWN",
        "PGUP": "PAGEUP", "PGDN": "PAGEDOWN", "INS": "INSERT", "DEL": "DELETE",
        "PLAYPAUSE": "MEDIA_PLAY", "NEXTTRACK": "MEDIA_NEXT",
        "PREVTRACK": "MEDIA_PREV", "MUTE": "VOLUME_MUTE",
        "VOLUP": "VOLUME_UP", "VOLDOWN": "VOLUME_DOWN",
    }
    return alias.get(n, n)


def make_token() -> str:
    return secrets.token_hex(8)


def key_names_from(mod) -> list:
    """从 keybridge 模块取键名清单（供前端自动补全）。"""
    names = set()
    try:
        names |= set(mod.NAME_TO_VK.keys())
    except Exception:                                            # noqa: BLE001
        pass
    names |= {"F13", "F14", "F15", "F16", "F17", "F18", "F19", "F20",
              "F21", "F22", "F23", "F24", "CTRL", "SHIFT", "ALT", "LWIN",
              "PRTSC", "SCROLLLOCK", "PAUSE", "INSERT"}
    return sorted(names)


def open_browser_later(url: str, delay: float = 1.2) -> None:
    def go():
        time.sleep(delay)
        try:
            os.startfile(url)                                    # noqa: S606
        except Exception:                                        # noqa: BLE001
            pass
    threading.Thread(target=go, daemon=True, name="open-browser").start()
