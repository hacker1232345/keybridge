#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_webuitest_run.py — Web 控制台 API 端到端测试

前提：keybridge.py -c tests/fixtures/webuitest.json 已经在跑（端口 8793）。
      KB_CFG=<你的配置路径> 可以覆盖要断言的配置文件。
只测 API 层，不需要装钩子、不需要提权。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = os.environ.get("KB_HOST") or "http://127.0.0.1:8793"
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.environ.get("KB_CFG") or os.path.join(HERE, "fixtures", "webuitest.json")
LOG = r"D:\kb-test.log"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra else ""))


def req(path, method="GET", body=None, raw=False):
    url = HOST + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            txt = resp.read().decode("utf-8", "replace")
            return resp.status, (txt if raw else json.loads(txt))
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt


print("=" * 66)
print("Web 控制台 API 测试")
print("=" * 66)

# ---- 1. 页面 ----
print("\n[1] 页面本身")
st, html = req("/", raw=True)
check("GET / 返回 200", st == 200, st)
check("返回的是 HTML", isinstance(html, str) and "<!doctype html>" in html.lower())
check("页面里有 CODE_TO_KEY 表", isinstance(html, str) and '"F13": "F13"' in html)
check("页面里有保存按钮", isinstance(html, str) and "保存并生效" in html)
check("占位符已被替换", isinstance(html, str) and "__CODE_TO_KEY__" not in html)
check("页面不是被截断的", isinstance(html, str) and html.rstrip().endswith("</html>"),
      "len=%d" % (len(html) if isinstance(html, str) else -1))

# ---- 2. 读状态 ----
print("\n[2] GET /api/state")
st, j = req("/api/state")
check("返回 200", st == 200, st)
check("ok=true", j.get("ok") is True, j)
cfg = j.get("config") or {}
check("拿到 hotkeys", isinstance(cfg.get("hotkeys"), list), len(cfg.get("hotkeys") or []))
check("hotkeys 数量 = 2", len(cfg.get("hotkeys") or []) == 2)
check("activeCount = 2", j.get("activeCount") == 2, j.get("activeCount"))
check("configPath 正确", str(j.get("configPath", "")).endswith("webuitest.json"), j.get("configPath"))
check("keyNames 含 F24", "F24" in (j.get("keyNames") or []))

# ---- 3. 试一下（执行动作）----
print("\n[3] POST /api/test（执行动作）")
if os.path.exists(LOG):
    os.remove(LOG)
st, j = req("/api/test", "POST", {"type": "shell", "shell": "cmd",
                                  "command": "echo WEBUI-TEST>>%s" % LOG})
check("返回 200", st == 200, st)
check("ok=true", j.get("ok") is True, j)
time.sleep(2.0)
txt = open(LOG, encoding="utf-8", errors="replace").read() if os.path.exists(LOG) else ""
check("动作真的执行了", "WEBUI-TEST" in txt, repr(txt[:80]))

# ---- 4. 保存（新增一条热键）----
print("\n[4] POST /api/config（保存并热重载）")
before = open(CFG, encoding="utf-8").read()
cfg["hotkeys"].append({
    "id": "web-c", "name": "网页新增的", "keys": ["CTRL", "SHIFT", "F12"],
    "trigger": "long", "swallow": True, "repeat": False, "longpress_ms": 800,
    "action": {"type": "code", "lang": "python",
               "code": "print('hello from webui')"},
})
st, j = req("/api/config", "POST", cfg)
check("返回 200", st == 200, st)
check("ok=true", j.get("ok") is True, j)
check("activeCount = 3", j.get("activeCount") == 3, j.get("activeCount"))
check("提示已备份", j.get("backup") is True, j.get("backup"))
time.sleep(0.5)
after = open(CFG, encoding="utf-8").read()
check("配置文件真的被改了", "web-c" in after)
check("旧文件备份存在", os.path.exists(CFG + ".bak"))
bak = open(CFG + ".bak", encoding="utf-8", errors="replace").read()
check("备份是旧内容（不含 web-c）", "web-c" not in bak)
st2, j2 = req("/api/state")
check("热重载后 state 也变了", len((j2.get("config") or {}).get("hotkeys") or []) == 3,
      len((j2.get("config") or {}).get("hotkeys") or []))

# ---- 5. 校验拦截 ----
print("\n[5] 非法配置要被拦住")
bad = json.loads(after)
bad["hotkeys"].append({"id": "web-a", "name": "重复ID", "keys": ["F1"], "action": {"type": "run", "program": "x"}})
st, j = req("/api/config", "POST", bad)
check("重复 id 被拒", st == 400 and j.get("ok") is False, (st, j.get("error")))
check("错误信息提到重复", "重复" in str(j.get("error", "")), j.get("error"))

bad2 = json.loads(after)
bad2["hotkeys"].append({"id": "nokeys", "name": "没按键", "keys": [], "action": {"type": "run", "program": "x"}})
st, j = req("/api/config", "POST", bad2)
check("空按键被拒", st == 400, (st, j.get("error")))

bad3 = json.loads(after)
bad3["hotkeys"].append({"id": "noact", "name": "没动作", "keys": ["F2"]})
st, j = req("/api/config", "POST", bad3)
check("缺动作被拒", st == 400, (st, j.get("error")))

after2 = open(CFG, encoding="utf-8").read()
check("被拒的请求没有污染文件", "nokeys" not in after2 and "noact" not in after2)

# ---- 6. 日志 ----
print("\n[6] GET /api/log")
st, j = req("/api/log?n=50")
check("返回 200", st == 200, st)
check("拿到日志文本", isinstance(j.get("log"), str) and len(j["log"]) > 0,
      "len=%d" % len(j.get("log") or ""))
# 日志尾部滚动很快（DEBUG 级会记每个按键），所以只断言"有可识别的关键行"：
# 要么含启动期的热键加载记录，要么含运行期的 KEY/DOWN/UP 事件。
_lg = j.get("log") or ""
check("日志内容可识别",
      ("web-a" in _lg) or ("KEY " in _lg) or ("DOWN " in _lg) or ("Web 控制台" in _lg),
      "前 80 字: %r" % _lg[:80])

# ---- 7. 重新加载 ----
print("\n[7] POST /api/reload")
st, j = req("/api/reload", "POST")
check("返回 200", st == 200, st)
check("ok=true", j.get("ok") is True, j)

# ---- 8. 删回去 ----
print("\n[8] 收尾：把测试新增的删掉")
st, j = req("/api/state")
cfg2 = j["config"]
cfg2["hotkeys"] = [h for h in cfg2["hotkeys"] if h["id"] != "web-c"]
st, j = req("/api/config", "POST", cfg2)
check("恢复成 2 条", st == 200 and j.get("activeCount") == 2, (st, j.get("activeCount")))

print("\n" + "=" * 66)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败清单：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("控制台 API 结论: 全部通过 ✅")
