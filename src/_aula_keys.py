# -*- coding: utf-8 -*-
"""导出「Aula Hub 改键页真正能选的键」全表，并给出 keybridge 侧的写法。

用法：
    python _aula_keys.py                     # 打印对照表
    python _aula_keys.py > aula-keys.txt     # 存文件

数据来源（两条独立的表，互为印证）：
  asar/dist/assets/DEdNpXFo.js   ← 改键页 UI 用的表，带显示文字
  asar/dist/assets/BS844IfY.js   ← 另一份更紧凑的表
两份都不含 F13–F24 —— 这就是「驱动里改不了 F13」的原因。

脱机使用：仓库自带 data/aula-keys.json（已 dump 好的 114 条）。
没有这个文件、也没有 Aula Hub 安装目录时，脚本会直接用内置表，仍然能出结果。
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
BUNDLED = os.path.join(_ROOT, "data", "aula-keys.json")

# 允许用环境变量指向自己的 Aula Hub 安装目录
AULA_RE = os.environ.get("AULA_RE") or os.path.join(_ROOT, "aula-re")

# Aula 表里的 code 名 → keybridge 键名
CODE2KB = {
    "Escape": "ESC", "Backquote": "`", "Minus": "-", "Equal": "=", "Backspace": "BACKSPACE",
    "Tab": "TAB", "BracketLeft": "[", "BracketRight": "]", "Backslash": "\\",
    "CapsLock": "CAPSLOCK", "Semicolon": ";", "Quote": "'", "Enter": "ENTER",
    "ShiftLeft": "LSHIFT", "ShiftRight": "RSHIFT", "ControlLeft": "LCTRL", "ControlRight": "RCTRL",
    "MetaLeft": "LWIN", "MetaRight": "RWIN", "AltLeft": "LALT", "AltRight": "RALT",
    "ContextMenu": "APPS", "Space": "SPACE", "PrintScreen": "PRTSC", "ScrollLock": "SCROLLLOCK",
    "Pause": "PAUSE", "Insert": "INSERT", "Home": "HOME", "PageUp": "PAGEUP",
    "Delete": "DELETE", "End": "END", "PageDown": "PAGEDOWN",
    "ArrowUp": "UP", "ArrowLeft": "LEFT", "ArrowDown": "DOWN", "ArrowRight": "RIGHT",
    "NumLock": "NUMLOCK", "NumpadDivide": "NUMDIV", "NumpadMultiply": "NUMMUL",
    "NumpadSubtract": "NUMSUB", "NumpadAdd": "NUMADD", "NumpadDecimal": "NUMDEC",
    "NumpadEnter": "NUMENTER", "Numpad0": "NUM0", "Numpad1": "NUM1", "Numpad2": "NUM2",
    "Numpad3": "NUM3", "Numpad4": "NUM4", "Numpad5": "NUM5", "Numpad6": "NUM6",
    "Numpad7": "NUM7", "Numpad8": "NUM8", "Numpad9": "NUM9", "Apps": "APPS",
}
for _i in range(1, 25):
    CODE2KB["F%d" % _i] = "F%d" % _i
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    CODE2KB["Key" + _c] = _c
for _i in range(10):
    CODE2KB["Digit%d" % _i] = str(_i)


def load_codes(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    re1 = re.compile(r"'([A-Za-z0-9]+)':\{'value':(0x[0-9a-fA-F]+),'type':(0x[0-9a-fA-F]+)\}")
    re2 = re.compile(r"'([A-Za-z0-9]+)':\{'value':(0x[0-9a-fA-F]+),'text':")
    out = {}
    for m in re1.finditer(s):
        out.setdefault(m.group(1), int(m.group(2), 16))
    for m in re2.finditer(s):
        out.setdefault(m.group(1), int(m.group(2), 16))
    return out


def load_all():
    """优先读 Aula Hub 安装目录里的原始表；读不到就用仓库自带的 dump。

    返回 (name -> hid 码, 来源说明)
    """
    ui_f = os.path.join(AULA_RE, "asar/dist/assets/DEdNpXFo.js")
    raw_f = os.path.join(AULA_RE, "asar/dist/assets/BS844IfY.js")
    if os.path.isfile(ui_f) and os.path.isfile(raw_f):
        ui = load_codes(ui_f)
        raw = load_codes(raw_f)
        allc = dict(raw)
        allc.update(ui)
        return allc, "DEdNpXFo.js(UI) %d 条 / BS844IfY.js %d 条 / 合并 %d 条" % (
            len(ui), len(raw), len(allc))

    if os.path.isfile(BUNDLED):
        d = json.load(open(BUNDLED, encoding="utf-8"))
        return {k: v["hid"] for k, v in d.items()}, "data/aula-keys.json（已 dump 的 %d 条）" % len(d)

    return {}, "没找到键位表（设 AULA_RE 环境变量指向 Aula Hub 的 asar 解包目录）"


def main():
    allc, src = load_all()

    print("Aula Hub 改键页可选键全表")
    print("=" * 72)
    print("表来源：%s" % src)
    print("F13–F24 在上面哪张表里？ %s"
          % ("都没有" if not any(re.fullmatch(r"F1[3-9]|F2[0-4]", k) for k in allc) else "有"))
    print("=" * 72)

    if not allc:
        print("没有数据。请设置 AULA_RE 环境变量，例如：")
        print('  set AULA_RE=D:\\AulaHubUnpack\\app')
        print("或者把 data/aula-keys.json 放回来。")
        return 1

    print("%-18s %-8s %-14s" % ("Aula 里的名字", "HID 码", "keybridge 写法"))
    print("-" * 72)

    missing = []
    for name in sorted(allc, key=lambda n: (allc[n], n)):
        kb = CODE2KB.get(name)
        if kb is None:
            missing.append(name)
            kb = "(未映射)"
        print("%-18s 0x%02X     %-14s" % (name, allc[name], kb))

    if missing:
        print("\n未映射的 %d 个（keybridge 键名可能要自己试）：%s"
              % (len(missing), ", ".join(missing)))

    print("\n" + "=" * 72)
    print("推荐拿来做触发的键（Aula 能选 + 平时几乎不按）")
    print("-" * 72)
    for kb, why in [("SCROLLLOCK", "绝大多数游戏/软件不用"), ("PAUSE", "同上"),
                    ("INSERT", "几乎没人用"), ("PRTSC", "改掉后系统截图归你管"),
                    ("APPS", "菜单键，很少按"), ("NUMLOCK", "注意会影响小键盘"),
                    ("HOME", "文本编辑里有用，看场景"), ("END", "同上")]:
        print("  %-12s %s" % (kb, why))
    print()
    print("注意 F1–F12 虽然能选，但游戏/软件里常被占用，慎用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
