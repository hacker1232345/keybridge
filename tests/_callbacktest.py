# -*- coding: utf-8 -*-
"""钩子回调层真实测试：直接调用 HookEngine._callback，喂真实结构的 KBDLLHOOKSTRUCT。

这是「按键 → 动作」链路里唯一没有被 UIPI 影响、又能真实跑到的部分：
  SetWindowsHookExW 的系统回调 → ctypes 解析 → 过滤注入 → _on_down/_on_up → 派发动作
本测试用真实内存结构体走完整条路径（而非绕过 callback 直接调 _on_down），
因此能覆盖参数编组、结构体偏移、吞键返回值、注入过滤这些只有在真实回调里才会踩的坑。

与 _logictest.py 的分工：那个测「引擎状态机」，这个测「钩子入口 + 结构体解析」。

注意：_dispatch 走线程池异步执行，断言前必须用 wait_calls() 等排空。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)   # keybridge.py 与配置所在的上一级目录
sys.path.insert(0, ROOT)

import keybridge as kb                                                    # noqa: E402

PASS = FAIL = 0
FAILS = []


def check(label, got, want):
    global PASS, FAIL
    ok = (got == want)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(label)
    print("  %-50s got=%-30s want=%-30s %s"
          % (label, repr(got), repr(want), "PASS" if ok else "FAIL"))


class Recorder:
    """替身 ActionRunner：只记录被派发的动作，不真的执行。"""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def run(self, action, ctx=None):
        with self.lock:
            self.calls.append((action.get("tag"), (ctx or {}).get("phase")))

    def snapshot(self):
        with self.lock:
            return list(self.calls)


def wait_calls(rec, n=1, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and len(rec.snapshot()) < n:
        time.sleep(0.01)
    time.sleep(0.08)
    return rec.snapshot()


class FakeKb(kb.KBDLLHOOKSTRUCT):
    pass


def make_kb(vk, flags):
    b = FakeKb()
    b.vkCode = vk
    b.scanCode = 0
    b.flags = flags
    b.time = 0
    b.dwExtraInfo = 0
    return b


def fire(engine, down, vk, flags=0):
    """真实调用钩子回调，返回是否吞键（1=吞）。"""
    b = make_kb(vk, flags)
    p = ctypes.cast(ctypes.pointer(b), ctypes.c_void_p).value
    msg = kb.WM_KEYDOWN if down else kb.WM_KEYUP
    return engine._callback(0, msg, p)


def new_engine(filter_injected=True):
    rec = Recorder()
    hks = [
        kb.Hotkey({"id": "k-f13", "keys": "F13", "swallow": True,
                   "action": {"type": "run", "tag": "F13"}}),
        kb.Hotkey({"id": "k-f14", "keys": "F14",
                   "action": {"type": "run", "tag": "F14"}}),
        kb.Hotkey({"id": "k-alt-f15", "keys": "Alt+F15", "swallow": True,
                   "action": {"type": "run", "tag": "ALTF15"}}),
    ]
    return kb.HookEngine(hks, rec, filter_injected=filter_injected), rec


print("[1] 结构体解析与吞键返回值")
eng, rec = new_engine()
r_down = fire(eng, True, 0x7C)
r_up = fire(eng, False, 0x7C)
check("F13(swallow) 按下 → 回调返回 1（吞键）", r_down, 1)
check("F13(swallow) 抬起 → 回调返回 1（吞键）", r_up, 1)
check("F13 动作被派发一次（phase=down）", wait_calls(rec, 1), [("F13", "down")])

print("\n[2] 非吞键热键的返回值与派发")
eng, rec = new_engine()
check("F14 按下 → 回调返回 0（不吞）", fire(eng, True, 0x7D), 0)
check("F14 抬起 → 回调返回 0（不吞）", fire(eng, False, 0x7D), 0)
check("F14 动作被派发", wait_calls(rec, 1), [("F14", "down")])

print("\n[3] 组合键（含修饰键状态维护）")
eng, rec = new_engine()
check("Alt 单独按下 → 未匹配任何热键，不吞", fire(eng, True, 0x12), 0)
check("Alt 按住时按 F15 → 命中 Alt+F15 并吞键", fire(eng, True, 0x7E), 1)
check("  → 派发 Alt+F15", wait_calls(rec, 1), [("ALTF15", "down")])
fire(eng, False, 0x7E)
fire(eng, False, 0x12)
check("松开后状态清空（再按 F15 不再触发）",
      (fire(eng, True, 0x7E), rec.snapshot()), (0, [("ALTF15", "down")]))

print("\n[4] 注入标志过滤（filter_injected=True 是默认，防止自己触发自己）")
eng, rec = new_engine(filter_injected=True)
check("LLKHF_INJECTED(0x10) 的按下被忽略",
      (fire(eng, True, 0x7C, flags=0x10), rec.snapshot()), (0, []))
check("LLKHF_LOWER_IL_INJECTED(0x02) 的按下被忽略",
      (fire(eng, True, 0x7C, flags=0x02), rec.snapshot()), (0, []))
check("真实按键(flags=0) 正常放行",
      (fire(eng, True, 0x7C, flags=0), wait_calls(rec, 1)), (1, [("F13", "down")]))

print("\n[5] filter_injected=False 时注入按键也被响应（--allow-injected）")
eng, rec = new_engine(filter_injected=False)
check("注入按键被放行",
      (fire(eng, True, 0x7C, flags=0x10), wait_calls(rec, 1)), (1, [("F13", "down")]))

print("\n[6] 非 0 的 n_code 必须立即放行（Windows 规约）")
eng, rec = new_engine()
b = make_kb(0x7C, 0)
p = ctypes.cast(ctypes.pointer(b), ctypes.c_void_p).value
check("n_code=3 → 返回 0 且不派发",
      (eng._callback(3, kb.WM_KEYDOWN, p), rec.snapshot()), (0, []))

print("\n[7] 系统自动重复（按住不放的连发）")
eng, rec = new_engine()
fire(eng, True, 0x7D)
n1 = len(wait_calls(rec, 1))
fire(eng, True, 0x7D)          # 系统重复
fire(eng, True, 0x7D)
time.sleep(0.2)
check("repeat=false：重复按下不重复派发", len(rec.snapshot()), n1)
fire(eng, False, 0x7D)
fire(eng, True, 0x7D)
check("松开后再按 → 第二次派发", len(wait_calls(rec, n1 + 1)), n1 + 1)

print("\n[8] 真实结构体字节布局（防止 ctypes 字段错位）")
b = make_kb(0x7C, 0x10)
raw = bytes(bytearray(ctypes.string_at(ctypes.byref(b), 24)))
check("结构体大小 = 24 字节（x64）", ctypes.sizeof(kb.KBDLLHOOKSTRUCT), 24)
check("vkCode 位于偏移 0", int.from_bytes(raw[0:4], "little"), 0x7C)
check("flags  位于偏移 8", int.from_bytes(raw[8:12], "little"), 0x10)
b2 = make_kb(0x41, 0)
raw2 = bytes(bytearray(ctypes.string_at(ctypes.byref(b2), 24)))
check("dwExtraInfo 位于偏移 16（8 字节）", int.from_bytes(raw2[16:24], "little"), 0)

print("\n[9] 真实钩子安装（SetWindowsHookExW 走一遍，验证可安装可卸载）")
try:
    eng = kb.HookEngine([], Recorder())
    eng.install()
    check("SetWindowsHookExW 安装成功", eng._hook is not None, True)
    eng.uninstall()
    check("UnhookWindowsHookEx 卸载成功", eng._hook is None, True)
except OSError as e:
    check("SetWindowsHookExW 安装成功", "异常: %s" % e, True)

print("\n[10] probe 模式能拿到键名（用回调直接喂）")
eng, rec = new_engine()
got = []
eng.probe = True
eng.probe_cb = lambda vk: got.append(kb.name_of(vk))
for vk in (0x7C, 0x7D, 0x41, 0x0D, 0xA0, 0x5B):
    fire(eng, True, vk)
check("probe 收到 6 个键名", got, ["F13", "F14", "A", "ENTER", "LSHIFT", "LWIN"])

print("\n" + "=" * 96)
print("钩子回调层：通过 %d 项，失败 %d 项" % (PASS, FAIL))
if FAILS:
    print("失败清单:")
    for f in FAILS:
        print("   -", f)
print("=" * 96)
sys.exit(1 if FAIL else 0)
