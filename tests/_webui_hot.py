#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_webui_hot.py — 验证「网页保存 → 热重载 → 新热键真的生效」

流程：
  1. 从 /api/state 读当前配置
  2. 通过 /api/config 新增一条热键 kb-hot-check（F20 → 写文件）
  3. 轮询 /api/state 确认引擎里已经有它（activeCount +1）
  4. 用 HTTP 触发 /t/kb-hot-check，确认文件被写（说明新热键的 action 真的能跑）
  5. 删回原样
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8795
BASE = "http://127.0.0.1:%d" % PORT
MARK = r"D:\kb-hot-reload.txt"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra else ""))


def req(path, method="GET", body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


print("=" * 66)
print("网页保存 → 热重载 → 新热键生效  链路测试")
print("=" * 66)

if os.path.exists(MARK):
    os.remove(MARK)

print("\n[1] 记录当前状态")
st, j = req("/api/state")
check("能读到 state", st == 200, st)
n0 = j.get("activeCount")
cfg = j["config"]
check("当前生效条数已知", isinstance(n0, int), n0)

print("\n[2] 走 /api/config 新增一条热键（等价于网页点「保存并生效」）")
cfg["hotkeys"].append({
    "id": "kb-hot-check",
    "name": "热重载验证",
    "keys": ["F20"],
    "trigger": "down",
    "swallow": True,
    "action": {"type": "shell", "shell": "cmd",
               "command": "echo HOT-OK>>%s" % MARK},
})
st, j = req("/api/config", "POST", cfg)
check("保存返回 200", st == 200, st)
check("activeCount 变成 %d" % (n0 + 1), j.get("activeCount") == n0 + 1, j.get("activeCount"))
check("没有警告", not (j.get("warnings") or []), j.get("warnings"))

print("\n[3] 隔 3 秒再查：确认引擎里真的留住了它")
time.sleep(3.0)
st, j = req("/api/state")
ids = [h.get("id") for h in (j.get("config") or {}).get("hotkeys", [])]
check("配置里有 kb-hot-check", "kb-hot-check" in ids, ids)
check("activeCount 仍然是 %d" % (n0 + 1), j.get("activeCount") == n0 + 1, j.get("activeCount"))

print("\n[4] 触发这条新热键（走 HTTP 触发通道，引擎用的就是热重载后的 hotkeys）")
st, j = req("/t/kb-hot-check")
check("触发返回 200", st == 200, (st, j))
check("触发 ok=true", j.get("ok") is True, j)
time.sleep(2.5)
txt = open(MARK, encoding="utf-8", errors="replace").read() if os.path.exists(MARK) else ""
check("新热键的动作真的执行了", "HOT-OK" in txt, repr(txt[:60]))

print("\n[5] 再改一次这条热键的动作（验证「改」也能生效），然后触发")
st, j = req("/api/state")
cfg2 = j["config"]
for h in cfg2["hotkeys"]:
    if h["id"] == "kb-hot-check":
        h["action"] = {"type": "shell", "shell": "cmd",
                       "command": "echo HOT-CHANGED>>%s" % MARK}
st, j = req("/api/config", "POST", cfg2)
check("改动作保存成功", st == 200 and j.get("ok") is True, (st, j.get("error")))
if os.path.exists(MARK):
    os.remove(MARK)
st, j = req("/t/kb-hot-check")
time.sleep(2.5)
txt = open(MARK, encoding="utf-8", errors="replace").read() if os.path.exists(MARK) else ""
check("改完的动作生效了（文件里是新内容）", "HOT-CHANGED" in txt and "HOT-OK" not in txt, repr(txt[:60]))

print("\n[6] 停用这条热键（enabled:false），确认它从生效列表里消失")
st, j = req("/api/state")
cfg3 = j["config"]
for h in cfg3["hotkeys"]:
    if h["id"] == "kb-hot-check":
        h["enabled"] = False
st, j = req("/api/config", "POST", cfg3)
check("停用后 activeCount 回到 %d" % n0, j.get("activeCount") == n0, j.get("activeCount"))

print("\n[7] 收尾：删掉测试热键")
st, j = req("/api/state")
cfg4 = j["config"]
cfg4["hotkeys"] = [h for h in cfg4["hotkeys"] if h["id"] != "kb-hot-check"]
st, j = req("/api/config", "POST", cfg4)
check("恢复成 %d 条" % n0, st == 200 and j.get("activeCount") == n0, (st, j.get("activeCount")))
if os.path.exists(MARK):
    os.remove(MARK)

print("\n" + "=" * 66)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("热重载结论: 全部通过 ✅")
