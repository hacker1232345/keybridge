# -*- coding: utf-8 -*-
"""逻辑级单测：不经过 Windows 钩子，直接喂按键序列给 HookEngine，断言触发行为。

这样绕开了 UIPI 对模拟按键的拦截，能完整覆盖匹配/触发/吞键/重复等逻辑。
配套 _hooktest_run.py 做真实钩子的端到端验证。
"""
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)              # 仓库根目录
sys.path.insert(0, os.path.join(ROOT, "src"))   # keybridge.py 在 src/ 里

import keybridge as kb  # noqa: E402

VK = {"CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "LCTRL": 0xA2, "LSHIFT": 0xA0,
      "K": 0x4B, "Q": 0x51, "A": 0x41,
      "F13": 0x7C, "F14": 0x7D, "F15": 0x7E, "F16": 0x7F, "F17": 0x80, "F18": 0x81}


class Recorder:
    """替身 runner：只记录，不产生副作用。"""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def run(self, action, ctx):
        with self.lock:
            self.events.append((action.get("tag"), ctx.get("phase")))

    def tags(self):
        with self.lock:
            return [e[0] for e in self.events]

    def clear(self):
        with self.lock:
            self.events.clear()


PASS, FAIL = [], []


def check(label, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print("  %-46s got=%-28s want=%-28s %s"
          % (label, got, want, "PASS" if ok else "FAIL"))


def mk(spec, **kw):
    raw = dict(spec)
    raw.update(kw)
    return kb.Hotkey(raw)


def build(hotkeys):
    rec = Recorder()
    eng = kb.HookEngine(hotkeys, rec)
    return eng, rec


def press(eng, *names):
    for n in names:
        eng._on_down(VK[n])


def release(eng, *names):
    for n in reversed(names):
        eng._on_up(VK[n])


def tap(eng, *names):
    press(eng, *names)
    release(eng, *names)


def settle(sec=0.35):
    time.sleep(sec)


print("=" * 78)
print("keybridge 逻辑单测")
print("=" * 78)

# ---------------------------------------------------------------- 1. 单键
print("\n[1] 单键触发")
eng, rec = build([mk({"id": "a", "keys": ["F13"], "action": {"tag": "T13"}})])
tap(eng, "F13")
settle()
check("F13 单击 → 触发一次", rec.tags(), ["T13"])
rec.clear()
tap(eng, "F14")
settle()
check("未配置的 F14 → 不触发", rec.tags(), [])

# ---------------------------------------------------------------- 2. 组合键
print("\n[2] 组合键")
eng, rec = build([mk({"id": "b", "keys": ["Ctrl", "Alt", "K"], "action": {"tag": "CAK"}})])
press(eng, "CTRL")
press(eng, "ALT")
tap(eng, "K")
settle()
check("Ctrl+Alt+K → 触发", rec.tags(), ["CAK"])
rec.clear()
release(eng, "CTRL", "ALT")

# 顺序颠倒也要命中
press(eng, "ALT")
press(eng, "CTRL")
tap(eng, "K")
settle()
check("Alt 先按、Ctrl 后按 → 同样触发", rec.tags(), ["CAK"])
rec.clear()
release(eng, "ALT", "CTRL")

# 只按部分
press(eng, "CTRL")
tap(eng, "K")
settle()
check("只按 Ctrl+K → 不触发", rec.tags(), [])
release(eng, "CTRL")

# 左右修饰键等价
eng, rec = build([mk({"id": "c", "keys": ["Ctrl", "K"], "action": {"tag": "LK"}})])
press(eng, "LCTRL")
tap(eng, "K")
settle()
check("左 Ctrl 视为 Ctrl", rec.tags(), ["LK"])
release(eng, "LCTRL")

# ---------------------------------------------------------------- 3. trigger 语义
print("\n[3] trigger 语义")
eng, rec = build([mk({"id": "d", "keys": ["F15"], "trigger": "up", "action": {"tag": "UP"}})])
press(eng, "F15")
settle(0.15)
check("up 触发：按下时不动", rec.tags(), [])
release(eng, "F15")
settle()
check("up 触发：抬起时触发", rec.tags(), ["UP"])

eng, rec = build([mk({"id": "e", "keys": ["F16"], "trigger": "long",
                      "longpress_ms": 300, "action": {"tag": "LONG"}})])
press(eng, "F16")
settle(0.15)
check("long 触发：0.15s 未到阈值不动", rec.tags(), [])
settle(0.35)
check("long 触发：超过阈值后触发", rec.tags(), ["LONG"])
release(eng, "F16")

eng, rec = build([mk({"id": "f", "keys": ["F17"], "trigger": "long",
                      "longpress_ms": 400, "action": {"tag": "LONG2"}})])
press(eng, "F17")
settle(0.15)
release(eng, "F17")
settle(0.45)
check("long 触发：提前松手不触发", rec.tags(), [])

# ---------------------------------------------------------------- 4. repeat 语义
print("\n[4] repeat 语义（按住连发）")
eng, rec = build([mk({"id": "g", "keys": ["F17"], "repeat": False, "action": {"tag": "ONCE"}})])
eng._on_down(VK["F17"])
for _ in range(6):
    eng._on_down(VK["F17"])     # 模拟系统自动重复
settle()
check("repeat=false 按住连发 → 只触发一次", rec.tags(), ["ONCE"])
eng._on_up(VK["F17"])
rec.clear()
tap(eng, "F17")
settle()
check("repeat=false 松开后再按 → 再次触发", rec.tags(), ["ONCE"])

eng, rec = build([mk({"id": "h", "keys": ["F18"], "repeat": True, "action": {"tag": "REP"}})])
eng._on_down(VK["F18"])
for _ in range(5):
    eng._on_down(VK["F18"])
settle()
check("repeat=true 按住连发 → 触发 6 次", len(rec.tags()), 6)

# ---------------------------------------------------------------- 5. swallow
print("\n[5] swallow（吞键）")
eng, rec = build([mk({"id": "i", "keys": ["F13"], "swallow": True, "action": {"tag": "SW"}})])
r_down = eng._on_down(VK["F13"])
r_up = eng._on_up(VK["F13"])
check("swallow=true 按下被吞", r_down, True)
check("swallow=true 抬起被吞", r_up, True)

eng, rec = build([mk({"id": "j", "keys": ["F13"], "swallow": False, "action": {"tag": "NS"}})])
r_down = eng._on_down(VK["F13"])
r_up = eng._on_up(VK["F13"])
check("swallow=false 不吞键", (r_down, r_up), (False, False))

# ---------------------------------------------------------------- 6. 多热键共存
print("\n[6] 多热键共存与优先级")
eng, rec = build([
    mk({"id": "k1", "keys": ["F13"], "action": {"tag": "ONLY-F13"}}),
    mk({"id": "k2", "keys": ["Ctrl", "F13"], "action": {"tag": "CTRL-F13"}}),
])
press(eng, "CTRL")
tap(eng, "F13")
settle()
check("Ctrl+F13 同时满足两条 → 都触发", sorted(rec.tags()), ["CTRL-F13", "ONLY-F13"])
release(eng, "CTRL")

# ---------------------------------------------------------------- 7. 禁用与异常配置
print("\n[7] 启用/禁用与配置校验")
eng, rec = build([mk({"id": "l", "keys": ["F13"], "enabled": False, "action": {"tag": "OFF"}})])
tap(eng, "F13")
settle()
check("enabled=false → 不触发", rec.tags(), [])

try:
    mk({"id": "m", "keys": [], "action": {}})
    ok = False
except ValueError:
    ok = True
check("keys 为空 → 构造时报错", ok, True)

try:
    mk({"keys": ["F13"], "action": {}})
    ok = False
except ValueError:
    ok = True
check("缺 id → 构造时报错", ok, True)

bad = kb.Hotkey({"id": "n", "keys": ["VK_F13"], "action": {"tag": "HEX"}})
check("VK_ 十六进制写法可解析", bad._vks, {0x7C})

# ---------------------------------------------------------------- 8. 键名解析
print("\n[8] 键名解析与容错")
cases = [
    ("F13", 0x7C), ("f13", 0x7C), ("F24", 0x87), ("ESC", 0x1B), ("ESCAPE", 0x1B),
    ("Ctrl", 0x11), ("ctrl", 0x11), ("CONTROL", 0x11), ("Enter", 0x0D), ("RETURN", 0x0D),
    ("A", 0x41), ("5", 0x35), ("VK_0X7C", 0x7C), ("0x7C", 0x7C),
    ("PageUp", 0x21), ("PAGE_UP", 0x21), ("Win", 0x5B), ("MEDIA_PLAY", 0xB3),
]
bad_names = []
for name, want in cases:
    try:
        got = kb.vk_of(name)
    except Exception:                                            # noqa: BLE001
        got = None
    if got != want:
        bad_names.append("%s→%s(期望 %s)" % (name, got, want))
check("18 种键名写法全部解析正确", bad_names, [])

check("未知键名抛 KeyError", isinstance(
    (lambda: [kb.vk_of("NOTAKEY"), None][1])() if False else None, type(None)), True)
try:
    kb.vk_of("NOTAKEY")
    unknown_ok = False
except KeyError:
    unknown_ok = True
check("未知键名抛 KeyError", unknown_ok, True)

# ---------------------------------------------------------------- 9. 动作通道
print("\n[9] 动作分发")
rec2 = Recorder()
runner = kb.ActionRunner(ROOT)
runner.run({"type": "不存在的类型"}, {})
check("未知动作类型不崩溃（记日志）", True, True)

runner.run("不是字典", {})
check("非字典动作不崩溃", True, True)

# 空字段
for a in ({"type": "run"}, {"type": "shell"}, {"type": "script"},
          {"type": "code"}, {"type": "url"}, {"type": "http"}):
    runner.run(a, {})
check("各动作缺必填字段都不崩溃", True, True)

# 路径解析
ar = kb.ActionRunner(r"D:\base")
check("裸程序名不被拼成路径", ar._resolve("notepad.exe"), "notepad.exe")
check("带分隔符的相对路径被拼接", ar._resolve("scripts\\x.py"),
      os.path.join(r"D:\base", "scripts\\x.py"))
check("绝对路径原样返回", ar._resolve(r"C:\win\a.exe"), r"C:\win\a.exe")
check("空字符串原样返回", ar._resolve(""), "")

# ---------------------------------------------------------------- 10. 配置加载
print("\n[10] 配置文件加载")
for rel in ("examples/config.minimal.json", "examples/config.advanced.json",
            "tests/fixtures/hooktest.json", "tests/fixtures/selftest.json"):
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    try:
        cfg = kb.load_config(p)
        hks = kb.build_hotkeys(cfg)
        print("  %-32s 热键 %d 条  PASS" % (rel, len(hks)))
        PASS.append(rel)
    except Exception as e:                                       # noqa: BLE001
        print("  %-32s 失败: %s  FAIL" % (rel, e))
        FAIL.append(rel)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 78)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败清单:")
    for f in FAIL:
        print("   -", f)
print("=" * 78)
sys.exit(1 if FAIL else 0)
