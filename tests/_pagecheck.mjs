// _pagecheck.mjs — 把控制台页面里的 <script> 抠出来，交给 node 做语法检查
import http from 'node:http';

const url = process.argv[2] || 'http://127.0.0.1:8793/';

const html = await new Promise((res, rej) => {
  http.get(url, r => {
    let s = '';
    r.setEncoding('utf8');
    r.on('data', c => s += c);
    r.on('end', () => res(s));
  }).on('error', rej);
});

console.log('页面字节数:', Buffer.byteLength(html, 'utf8'));
console.log('有 doctype:', /^<!doctype html>/i.test(html.trim()));
console.log('以 </html> 结尾:', html.trimEnd().endsWith('</html>'));

// 抠出最后一个 <script> 块（页面里只有一个）
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.log('❌ 找不到 <script> 块'); process.exit(1); }
const js = m[1];
console.log('script 字节数:', Buffer.byteLength(js, 'utf8'));

// 写临时文件交给 node --check
const { writeFileSync, unlinkSync } = await import('node:fs');
const tmp = '_pagecheck_tmp.mjs';
writeFileSync(tmp, js, 'utf8');

// 用 new Function 做真正的解析检查（含语法错误会抛）
try {
  new Function(js.replace(/^\s*const CODE_TO_KEY[^\n]*\n/m, 'const CODE_TO_KEY = {};\n'));
  console.log('✅ JS 语法检查通过');
} catch (e) {
  console.log('❌ JS 语法错误:', e.message);
  process.exit(1);
}

// 静态自检：关键函数/元素是否存在
const need = ['function save(', 'function render(', 'function actionFields(',
  'function startRec(', 'function loadState(', 'btnSave', 'btnAdd', 'CODE_TO_KEY',
  "api('/api/config'", "api('/api/state'", 'ACTION_TYPES', 'TRIGGERS'];
for (const n of need) {
  console.log((js.includes(n) ? '  ✅ ' : '  ❌ ') + n);
  if (!js.includes(n)) process.exitCode = 1;
}

// 检查有没有残留的未替换占位符或明显的模板串逃逸
const bad = ['__CODE_TO_KEY__', 'undefined,"', "\\'", '${i}\',\''];
for (const b of bad) {
  if (js.includes(b)) { console.log('  ⚠ 可疑残留: ' + JSON.stringify(b)); }
}
unlinkSync(tmp);
console.log('done');
