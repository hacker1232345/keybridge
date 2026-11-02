#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keybridge — 按键桥 v1.0

把「某个按键 / 组合键」变成触发器：按下后启动指定进程、执行指定命令、运行指定代码。
配套 Aula Hub（狼蛛外设驱动）的改键功能使用：先在 Aula Hub 里把某个键映射成 F13~F24
（或任意少见键），再在本程序里给这个键绑定你要的动作。

用法:
    python keybridge.py                     用同目录 config.json 启动
    python keybridge.py -c my.json          指定配置文件
    python keybridge.py --web               启动后自动打开浏览器控制台
    python keybridge.py --test <hotkey-id>  不装钩子，只跑一次动作（自测用）
    python keybridge.py --probe             按键探测模式：打印你按的键名，用来确定键名
    python keybridge.py --list              列出配置里所有热键

Web 控制台:
    启动后打开 http://127.0.0.1:8765/?t=<token>（token 见启动日志）。
    在浏览器里增删改热键、点「保存并生效」直接写回配置文件并热重载，不用手改 JSON。

依赖: 无。纯 Python 标准库 + ctypes。
权限: 普通用户即可（低级键盘钩子 WH_KEYBOARD_LL 不需要管理员）。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "keybridge"
APP_VER = "1.0"
__version__ = APP_VER


def _app_dir() -> str:
    """程序目录。

    源码运行时是 .py 所在目录；PyInstaller 打包成单文件 exe 后 __file__ 指向
    临时解包目录（_MEIPASS），配置 / 日志 / 运行期文件必须落在 exe 旁边，
    否则每次启动都是一份全新的空配置。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_dir()

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# 关键：64 位下必须声明 restype/argtypes，否则默认按 32 位 int 截断句柄，
# SetWindowsHookExW 会拿到被截断的 hMod 并以错误码 126 (ERROR_MOD_NOT_FOUND) 失败。
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]

# ShellExecuteW 的返回值是 HINSTANCE，64 位下不声明 restype 会被截断成 32 位；
# 参数全是宽字符指针，不声明 argtypes 时中文路径会被当窄字符串传进去。
shell32.ShellExecuteW.restype = ctypes.c_void_p
shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR,
                                  wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wt.HMODULE, wt.DWORD]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]

# ---------------------------------------------------------------- 常量

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105

LLKHF_INJECTED = 0x00000010
LLKHF_LOWER_IL_INJECTED = 0x00000002

DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# 实测结论（本机 PowerShell 5.1）：
#   DETACHED_PROCESS 会让 powershell.exe 拿到一个"没有控制台也没有新控制台"的环境，
#   它会在执行 -Command 之前就静默退出（进程在、脚本没跑、无任何报错）。
#   CREATE_NO_WINDOW 或 CREATE_NEW_CONSOLE 都能正常执行，且都不弹窗。
#   因此默认用 CREATE_NO_WINDOW，需要真实控制台（可交互的 CLI）时用 console: true。
#   CREATE_BREAKAWAY_FROM_JOB 需要额外权限，普通用户会拿 WinError 5，不要用。

KEYEVENTF_KEYUP = 0x0002

# HTTP/控制台端口被占用时往后试几个。常见占用者：
#   HeadsetService(耳机驱动) 抢 8765、Hyper-V/WSL 保留端口段、其它本地调试服务。
# WinError 10013 = 端口被系统保留(excludedportrange)，10048 = 已被别人 LISTENING。
PORT_TRY = 8

# 启动子进程前必须剥离的环境变量。
# ELECTRON_RUN_AS_NODE=1 会让任何基于 Electron 的程序（VS Code 系、Aula Hub、
# 各种自研客户端）以「纯 Node 解释器」模式启动：不开窗口、不报错、退出码 0。
# 从外面看就是「点了没反应」——极难排查。
# DSH 之类的宿主进程会导出这些变量，被 keybridge 继承后会原样传给子进程。
ELECTRON_STRIP_ENV = (
    "ELECTRON_RUN_AS_NODE",
    "ELECTRON_NO_ATTACH_CONSOLE",
    "ELECTRON_ENABLE_LOGGING",
    "ELECTRON_ENABLE_STACK_DUMPING",
    "ELECTRON_FORCE_WINDOW_MENU_BAR",
    "ELECTRON_DEFAULT_ERROR_MODE",
    "ELECTRON_OVERRIDE_DIST_PATH",
    "NODE_OPTIONS",
)

# ---------------------------------------------------------------- 虚拟键表

VK_NAMES: dict[int, str] = {
    0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER", 0x10: "SHIFT", 0x11: "CTRL",
    0x12: "ALT", 0x13: "PAUSE", 0x14: "CAPSLOCK", 0x1B: "ESC", 0x20: "SPACE",
    0x21: "PAGEUP", 0x22: "PAGEDOWN", 0x23: "END", 0x24: "HOME",
    0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN",
    0x2C: "PRTSC", 0x2D: "INSERT", 0x2E: "DELETE", 0x5D: "APPS",
    0x5B: "LWIN", 0x5C: "RWIN",
    0x60: "NUM0", 0x61: "NUM1", 0x62: "NUM2", 0x63: "NUM3", 0x64: "NUM4",
    0x65: "NUM5", 0x66: "NUM6", 0x67: "NUM7", 0x68: "NUM8", 0x69: "NUM9",
    0x6A: "NUMMUL", 0x6B: "NUMADD", 0x6D: "NUMSUB", 0x6E: "NUMDEC", 0x6F: "NUMDIV",
    0x90: "NUMLOCK", 0x91: "SCROLLLOCK",
    0xA0: "LSHIFT", 0xA1: "RSHIFT", 0xA2: "LCTRL", 0xA3: "RCTRL",
    0xA4: "LALT", 0xA5: "RALT",
    0xA6: "BROWSER_BACK", 0xA7: "BROWSER_FORWARD", 0xA8: "BROWSER_REFRESH",
    0xA9: "BROWSER_STOP", 0xAA: "BROWSER_SEARCH", 0xAB: "BROWSER_FAVORITES",
    0xAC: "BROWSER_HOME",
    0xAD: "VOLUME_MUTE", 0xAE: "VOLUME_DOWN", 0xAF: "VOLUME_UP",
    0xB0: "MEDIA_NEXT", 0xB1: "MEDIA_PREV", 0xB2: "MEDIA_STOP", 0xB3: "MEDIA_PLAY",
    0xB4: "LAUNCH_MAIL", 0xB5: "LAUNCH_MEDIA", 0xB6: "LAUNCH_APP1", 0xB7: "LAUNCH_APP2",
    0xBA: "SEMICOLON", 0xBB: "EQUAL", 0xBC: "COMMA", 0xBD: "MINUS",
    0xBE: "PERIOD", 0xBF: "SLASH", 0xC0: "GRAVE",
    0xDB: "LBRACKET", 0xDC: "BACKSLASH", 0xDD: "RBRACKET", 0xDE: "QUOTE",
    0xE5: "IME_PROCESS",
}
for _i in range(0x30, 0x3A):                       # 0-9
    VK_NAMES[_i] = chr(_i)
for _i in range(0x41, 0x5B):                       # A-Z
    VK_NAMES[_i] = chr(_i)
for _i in range(0x70, 0x88):                       # F1-F24
    VK_NAMES[_i] = "F%d" % (_i - 0x6F)

ALIASES = {
    "ESCAPE": "ESC", "RETURN": "ENTER", "CONTROL": "CTRL", "MENU": "ALT",
    "WIN": "LWIN", "WINDOWS": "LWIN", "META": "LWIN", "SUPER": "LWIN",
    "PRINTSCREEN": "PRTSC", "PAGE_UP": "PAGEUP", "PAGE_DOWN": "PAGEDOWN",
    "PGUP": "PAGEUP", "PGDN": "PAGEDOWN", "INS": "INSERT", "DEL": "DELETE",
    "PLAYPAUSE": "MEDIA_PLAY", "NEXTTRACK": "MEDIA_NEXT", "PREVTRACK": "MEDIA_PREV",
    "MUTE": "VOLUME_MUTE", "VOLUP": "VOLUME_UP", "VOLDOWN": "VOLUME_DOWN",
    # Aula Hub 键位表里的名字 → 本程序键名
    "NUMPADENTER": "ENTER", "NUMPADDIVIDE": "NUMDIV", "NUMPADMULTIPLY": "NUMMUL",
    "NUMPADSUBTRACT": "NUMSUB", "NUMPADADD": "NUMADD", "NUMPADDECIMAL": "NUMDEC",
    "CONTEXTMENU": "APPS", "INTLBACKSLASH": "BACKSLASH", "BACKSLASH2": "BACKSLASH",
    "PRINTSCREEN": "PRTSC", "SCROLL": "SCROLLLOCK",
}
for _k in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
    ALIASES["NUM" + _k] = "NUM" + _k

NAME_TO_VK: dict[str, int] = {}
for _vk, _n in VK_NAMES.items():
    NAME_TO_VK.setdefault(_n, _vk)
NAME_TO_VK.update({
    "CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12,
    "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63,
})


def canon(name: str) -> str:
    """把键名归一化成大写标准名。"""
    n = str(name).strip().upper().replace(" ", "").replace("-", "")
    n = ALIASES.get(n, n)
    return n


def name_of(vk: int) -> str:
    return VK_NAMES.get(vk, "VK_0x%02X" % vk)


def vk_of(name: str) -> int:
    n = canon(name)
    if n in NAME_TO_VK:
        return NAME_TO_VK[n]
    if n.startswith("VK_"):
        rest = n[3:]
        if rest in NAME_TO_VK:          # VK_F13 → F13 → 0x7C
            return NAME_TO_VK[rest]
        return int(rest, 16)            # VK_7C → 0x7C
    if n.startswith("0X"):
        return int(n[2:], 16)
    # Aula Hub 键位表里的写法：KeyA / Digit5 / Numpad3
    if n.startswith("KEY") and len(n) == 4 and n[3].isalpha():
        return 0x41 + (ord(n[3]) - 0x41)
    if n.startswith("DIGIT") and len(n) == 6 and n[5].isdigit():
        return 0x30 + int(n[5])
    if n.startswith("NUMPAD") and len(n) == 7 and n[6].isdigit():
        return 0x60 + int(n[6])
    raise KeyError("未知键名: %s" % name)


def tags_of(vk: int) -> set[str]:
    """按下的一个键贡献的匹配标识。LCTRL 同时贡献 {LCTRL, CTRL}。"""
    n = name_of(vk)
    s = {n}
    if n in ("LCTRL", "RCTRL"):
        s.add("CTRL")
    elif n in ("LSHIFT", "RSHIFT"):
        s.add("SHIFT")
    elif n in ("LALT", "RALT"):
        s.add("ALT")
    elif n in ("LWIN", "RWIN"):
        s.add("WIN")
        s.add("LWIN")
    return s


# ---------------------------------------------------------------- 日志
#
# 重要（踩坑记录）：低级键盘钩子的回调运行在 Windows 的消息处理路径上。
# 如果回调里直接做文件 I/O（哪怕只是写一行 DEBUG 日志），一旦磁盘慢，
# 系统会认为这个钩子超时，把它静默摘掉 —— 表现是"跑几次之后完全没反应、
# 不报任何错"。所以钩子回调路径上的日志一律走 QueueHandler 入队，
# 由独立线程落盘，回调本身保持纯内存操作。

import atexit
import logging.handlers
import queue

log = logging.getLogger(APP_NAME)
_LOG_QUEUE: queue.Queue | None = None
_LOG_LISTENER: logging.handlers.QueueListener | None = None


def setup_log(cfg: dict, base_dir: str) -> None:
    global _LOG_QUEUE, _LOG_LISTENER
    level = getattr(logging, str(cfg.get("logLevel", "INFO")).upper(), logging.INFO)
    log.setLevel(level)
    log.handlers.clear()
    log.propagate = False

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    sinks: list[logging.Handler] = []

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.setLevel(level)
    sinks.append(sh)

    lf = cfg.get("logFile", "")
    if lf:
        p = lf if os.path.isabs(lf) else os.path.join(base_dir, lf)
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            fh = logging.FileHandler(p, encoding="utf-8")
            fh.setFormatter(fmt)
            fh.setLevel(level)
            sinks.append(fh)
        except Exception as e:                                  # noqa: BLE001
            sh.stream.write("日志文件不可写 (%s): %s\n" % (p, e))

    # 队列化：钩子回调只做 put_nowait，绝不碰 I/O
    _LOG_QUEUE = queue.Queue(-1)
    qh = logging.handlers.QueueHandler(_LOG_QUEUE)
    qh.setLevel(level)
    log.addHandler(qh)
    _LOG_LISTENER = logging.handlers.QueueListener(_LOG_QUEUE, *sinks, respect_handler_level=True)
    _LOG_LISTENER.start()
    atexit.register(_LOG_LISTENER.stop)


# ---------------------------------------------------------------- 动作执行

class ActionRunner:
    """把一条 action 变成真实副作用。所有方法都在工作线程里跑。"""

    def __init__(self, base_dir: str):
        self.base = base_dir

    # -- 工具 ---------------------------------------------------------

    def _python_exe(self) -> str:
        """找一个真正的 Python 解释器。

        打包成 keybridge.exe 后 sys.executable 指向 exe 自己，
        拿它去跑 .py 会变成「exe 不认识这个参数」→ 必须另找。
        顺序：环境变量 KEYBRIDGE_PYTHON → PATH 里的 python → py 启动器。
        """
        if not getattr(sys, "frozen", False):
            return sys.executable
        forced = os.environ.get("KEYBRIDGE_PYTHON")
        if forced and os.path.isfile(forced):
            return forced
        for name in ("python.exe", "python3.exe"):
            found = shutil.which(name)
            if found and os.path.abspath(found).lower() != os.path.abspath(sys.executable).lower():
                return found
        found = shutil.which("py.exe")
        if found:
            return found
        return "python"          # 交给 Popen 去报错，日志里能看见

    def _resolve(self, p: str) -> str:
        """相对路径按配置文件所在目录解析；裸程序名（notepad.exe）保持原样交给 PATH。"""
        if not p:
            return p
        if os.path.isabs(p):
            return p
        # 含路径分隔符或 ./ ../ 前缀 → 当作相对路径；否则当裸程序名
        if ("\\" in p) or ("/" in p) or p.startswith("."):
            return os.path.join(self.base, p)
        return p

    def _child_env(self, env=None) -> dict:
        """构造子进程环境，并剥掉「Electron/Node 注入类」变量。

        这些变量会让 Electron 应用（VS Code 系、Aula Hub、各类自研客户端）以纯 Node 模式启动：
        不开窗口、不报错、退出码 0 —— 从外面看就是「点了没反应」。
        DSH 之类的宿主进程会导出它们，被 keybridge 继承后原样传给子进程，于是踩坑。
        """
        base = dict(os.environ if env is None else {**os.environ, **env})
        for k in ELECTRON_STRIP_ENV:
            base.pop(k, None)
        return base

    def _popen(self, argv, cwd=None, hidden=False, detach=True, env=None, console=False):
        """启动子进程。

        hidden=True  无窗口后台执行（默认，适合命令/脚本）
        console=True 要一个真实控制台窗口（适合 cmd / 交互式 CLI）

        .lnk 快捷方式 / .url 网址文件 / 文档 这类「不是 PE 可执行文件」的目标不能走 CreateProcess，
        会报 WinError 193（%1 不是有效的 Win32 应用程序），必须交给 ShellExecute 打开。
        这里做两级处理：已知壳扩展名直接走过去，其它情况在 Popen 报 193 时兜底重试。
        """
        flags = 0
        if console:
            flags |= CREATE_NEW_CONSOLE
        elif hidden or detach:
            flags |= CREATE_NO_WINDOW
        extra = dict(creationflags=flags, close_fds=True)
        if not console:
            extra.update(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        child_env = self._child_env(env)
        dropped = [k for k in ELECTRON_STRIP_ENV if k in os.environ]
        if dropped:
            log.debug("已从子进程环境剥离: %s", ", ".join(dropped))
        log.info("启动: %s (cwd=%s, hidden=%s, console=%s)", argv, cwd or "", hidden, console)
        try:
            return subprocess.Popen(argv, cwd=cwd, env=child_env, **extra)
        except OSError as e:
            win = getattr(e, "winerror", None)
            target = argv[0] if argv else ""
            if win == 193 or (win == 2 and os.path.exists(target)):
                log.info("CreateProcess 打不开（WinError %s），改用 ShellExecute 打开: %s",
                         win, target)
                _shell_execute(target, params=" ".join(argv[1:]) or None, cwd=cwd)
                return None
            raise

    # -- 动作分发 -----------------------------------------------------

    def run(self, action: dict, ctx: dict | None = None) -> None:
        if not isinstance(action, dict):
            log.error("动作必须是对象，收到: %r", action)
            return
        kind = str(action.get("type", "run")).lower()
        fn = getattr(self, "_act_" + kind, None)
        if fn is None:
            log.error("不支持的动作类型: %s", kind)
            return
        try:
            fn(action, ctx or {})
        except Exception:                                        # noqa: BLE001
            log.exception("动作执行失败 (%s)", kind)

    # -- 1. run：启动进程 ---------------------------------------------

    def _act_run(self, a: dict, ctx: dict) -> None:
        prog = self._resolve(a.get("program") or a.get("exe") or "")
        if not prog:
            log.error("run 动作缺少 program")
            return
        args = a.get("args") or []
        if isinstance(args, str):
            args = [args]
        argv = [prog] + [str(x) for x in args]
        cwd = self._resolve(a.get("cwd") or "") or None
        self._popen(argv, cwd=cwd, hidden=bool(a.get("hidden", True)),
                    console=bool(a.get("console", False)))

    # -- 2. shell：执行命令 -------------------------------------------

    def _act_shell(self, a: dict, ctx: dict) -> None:
        cmd = a.get("command") or ""
        if not cmd:
            log.error("shell 动作缺少 command")
            return
        sh = str(a.get("shell", "cmd")).lower()
        cwd = self._resolve(a.get("cwd") or "") or None
        if sh in ("powershell", "pwsh", "ps"):
            exe = "pwsh" if sh == "pwsh" else "powershell"
            argv = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-Command", cmd]
        elif sh == "bash":
            argv = ["bash", "-lc", cmd]
        else:
            argv = ["cmd.exe", "/d", "/c", cmd]
        self._popen(argv, cwd=cwd, hidden=bool(a.get("hidden", True)),
                    console=bool(a.get("console", False)))

    # -- 3. script：运行脚本文件 ---------------------------------------

    def _act_script(self, a: dict, ctx: dict) -> None:
        f = self._resolve(a.get("file") or "")
        if not f:
            log.error("script 动作缺少 file")
            return
        interp = a.get("interpreter")
        if not interp:
            ext = os.path.splitext(f)[1].lower()
            interp = {".py": "python", ".ps1": "powershell", ".bat": "cmd",
                      ".cmd": "cmd", ".js": "node", ".mjs": "node",
                      ".vbs": "wscript", ".sh": "bash"}.get(ext, "cmd")
        interp = str(interp).lower()
        args = a.get("args") or []
        if isinstance(args, str):
            args = [args]
        args = [str(x) for x in args]
        cwd = self._resolve(a.get("cwd") or "") or os.path.dirname(f)
        if interp in ("python", "py"):
            argv = [self._python_exe(), "-u", f] + args
        elif interp in ("powershell", "pwsh", "ps"):
            exe = "pwsh" if interp == "pwsh" else "powershell"
            argv = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f] + args
        elif interp == "node":
            argv = ["node", f] + args
        elif interp == "wscript":
            argv = ["wscript.exe", f] + args
        elif interp == "bash":
            argv = ["bash", f] + args
        else:
            argv = ["cmd.exe", "/d", "/c", f] + args
        self._popen(argv, cwd=cwd, hidden=bool(a.get("hidden", True)),
                    console=bool(a.get("console", False)))

    # -- 4. code：运行内联代码 ----------------------------------------

    def _act_code(self, a: dict, ctx: dict) -> None:
        code = a.get("code") or ""
        if not code:
            log.error("code 动作缺少 code")
            return
        lang = str(a.get("lang", "python")).lower()
        ext = {"python": ".py", "py": ".py", "powershell": ".ps1", "ps": ".ps1",
               "bat": ".bat", "cmd": ".bat", "js": ".mjs", "node": ".mjs",
               "vbs": ".vbs"}.get(lang, ".txt")
        d = self._resolve(a.get("dir") or "runtime/tmp")
        os.makedirs(d, exist_ok=True)
        h = hashlib.sha1(code.encode("utf-8")).hexdigest()[:10]
        path = os.path.join(d, "inline_%s%s" % (h, ext))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(code)
        sub = dict(a)
        sub.update({"type": "script", "file": path,
                    "interpreter": {"py": "python", "ps": "powershell", "node": "node"}
                    .get(lang, lang)})
        self._act_script(sub, ctx)

    # -- 5. url：打开网址 / 本地路径 ----------------------------------

    def _act_url(self, a: dict, ctx: dict) -> None:
        url = a.get("url") or ""
        if not url:
            log.error("url 动作缺少 url")
            return
        if os.path.isabs(url) and os.path.exists(url):
            log.info("ShellExecute: %s", url)
            _shell_execute(url)
            return
        log.info("打开: %s", url)
        _shell_execute(url)

    # -- 6. keys：模拟按键 --------------------------------------------

    def _act_keys(self, a: dict, ctx: dict) -> None:
        spec = a.get("keys") or a.get("sequence") or ""
        delay = float(a.get("delay", 0.03))
        if isinstance(spec, list):
            for item in spec:
                send_keys(str(item), delay)
        else:
            send_keys(str(spec), delay)

    # -- 7. sequence：串行执行多个动作 --------------------------------

    def _act_sequence(self, a: dict, ctx: dict) -> None:
        for step in (a.get("steps") or []):
            self.run(step, ctx)
            d = float(step.get("waitAfter", 0) or 0) if isinstance(step, dict) else 0
            if d > 0:
                time.sleep(d)

    # -- 8. http：发一个 HTTP 请求 ------------------------------------

    def _act_http(self, a: dict, ctx: dict) -> None:
        url = a.get("url") or ""
        if not url:
            log.error("http 动作缺少 url")
            return
        method = str(a.get("method", "GET")).upper()
        data = a.get("body")
        body = None
        if data is not None:
            body = (data if isinstance(data, str) else json.dumps(data)).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in (a.get("headers") or {}).items():
            req.add_header(k, str(v))
        try:
            with urllib.request.urlopen(req, timeout=float(a.get("timeout", 10))) as r:
                log.info("http %s %s -> %s", method, url, r.status)
        except Exception as e:                                   # noqa: BLE001
            log.warning("http %s %s 失败: %s", method, url, e)

    # -- 9. script_dir：用资源管理器打开目录 --------------------------

    def _act_opendir(self, a: dict, ctx: dict) -> None:
        d = self._resolve(a.get("dir") or "")
        if d and os.path.isdir(d):
            _shell_execute(d)
        else:
            log.warning("目录不存在: %s", d)


def _shell_execute(target: str, params: str | None = None,
                   cwd: str | None = None, verb: str = "open") -> bool:
    """ShellExecuteW：打开网址 / 目录 / 文档 / .lnk 快捷方式。

    返回值 <= 32 表示失败（2 = 找不到文件，5 = 拒绝访问，31 = 无关联程序）。

    ShellExecuteW 没有 env 参数，新进程直接继承调用者的环境。所以这里先把
    Electron/Node 注入类变量临时摘掉再调，否则 .lnk 指向的 Electron 应用会以
    「纯 Node 模式」启动：不开窗口、退出码 0，看起来像点了没反应。
    """
    with _SHELL_ENV_LOCK:
        saved = {}
        for k in ELECTRON_STRIP_ENV:
            if k in os.environ:
                saved[k] = os.environ.pop(k)
        try:
            r = int(shell32.ShellExecuteW(None, verb, target, params, cwd, 1) or 0)
        finally:
            os.environ.update(saved)
    if r <= 32:
        hint = {
            2: "找不到文件",
            3: "找不到路径",
            5: "拒绝访问",
            31: "没有关联的打开方式",
        }.get(r, "")
        log.warning("ShellExecuteW 返回 %d%s（目标: %s，动作: %s）",
                    r, (" " + hint) if hint else "", target, verb)
        return False
    log.info("ShellExecute 已打开: %s", target)
    return True


# ---------------------------------------------------------------- 按键模拟

_SEND_LOCK = threading.Lock()

# ShellExecuteW 没有 env 参数，只能临时改本进程的 os.environ。
# 改环境是进程级操作，必须串行，否则并发打开会互相看到对方的临时状态。
_SHELL_ENV_LOCK = threading.Lock()


def send_keys(spec: str, delay: float = 0.03) -> None:
    """模拟按键。spec 形如 "ctrl+shift+s" 或 "{F13}" 或 "hello"。"""
    with _SEND_LOCK:
        i = 0
        while i < len(spec):
            if spec[i] == "+":
                i += 1
                continue
            if spec[i] == "{":
                j = spec.find("}", i)
                if j < 0:
                    break
                name = spec[i + 1:j]
                try:
                    vk = vk_of(name)
                    user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(delay)
                    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                except KeyError:
                    log.warning("send_keys 未知键: %s", name)
                i = j + 1
                time.sleep(delay)
                continue
            j = i
            while j < len(spec) and spec[j] not in "+{":
                j += 1
            token = spec[i:j]
            if token:
                vk = NAME_TO_VK.get(canon(token))
                if vk is None and len(token) == 1:
                    vk = user32.VkKeyScanW(ord(token)) & 0xFF
                if vk:
                    user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(delay)
                    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            i = j
            time.sleep(delay)


# ---------------------------------------------------------------- 热键引擎

class Hotkey:
    def __init__(self, raw: dict):
        self.raw = raw
        self.id = str(raw.get("id") or raw.get("name") or "")
        if not self.id:
            raise ValueError("hotkey 缺少 id")
        self.name = str(raw.get("name") or self.id)
        keys = raw.get("keys")
        if isinstance(keys, str):
            keys = [k for k in keys.replace("+", " ").split() if k]
        if not keys:
            raise ValueError("hotkey %s 缺少 keys" % self.id)
        self.keys = [canon(k) for k in keys]
        self.tags = set(self.keys)
        self._vks = {vk_of(k) for k in self.keys}
        self.trigger = str(raw.get("trigger", "down")).lower()
        self.swallow = bool(raw.get("swallow", False))
        self.repeat = bool(raw.get("repeat", False))
        self.longpress_ms = int(raw.get("longpress_ms", 600))
        self.enabled = bool(raw.get("enabled", True))
        self.action = raw.get("action") or {}
        self._fired = False
        self._timer: threading.Timer | None = None
        self._armed = False

    def matches(self, down_tags: set[str]) -> bool:
        return self.tags.issubset(down_tags)

    def __repr__(self) -> str:
        return "<Hotkey %s keys=%s trigger=%s>" % (self.id, "+".join(self.keys), self.trigger)


class HookEngine:
    """低级键盘钩子 + 热键匹配 + 动作派发。"""

    def __init__(self, hotkeys: list[Hotkey], runner: ActionRunner,
                 filter_injected: bool = True):
        self.hotkeys = hotkeys
        self.runner = runner
        self.down: set[int] = set()          # 当前按下的 vk
        self.tags: set[str] = set()          # 当前按下的标识并集
        self.swallow_vk: set[int] = set()    # 需要吞掉的 vk（keyup 时用）
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8,
                                                          thread_name_prefix="kb-act")
        self.filter_injected = filter_injected
        self.probe = False
        self.probe_cb = None
        self._hook = None
        self._proc = HOOKPROC(self._callback)   # 必须保持引用
        self.ready = threading.Event()

    # -- 派发 ---------------------------------------------------------

    def _dispatch(self, hk: Hotkey, phase: str) -> None:
        log.info("触发 [%s] %s (%s)", hk.id, hk.name, phase)
        self.pool.submit(self.runner.run, hk.action, {"id": hk.id, "phase": phase,
                                                      "at": time.time()})

    def _fire(self, hk: Hotkey, phase: str) -> None:
        if hk._fired and not hk.repeat:
            return
        hk._fired = True
        self._dispatch(hk, phase)

    # -- 钩子回调 -----------------------------------------------------

    def _callback(self, n_code, w_param, l_param):
        try:
            if n_code == 0:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(kb.vkCode)
                flags = int(kb.flags)
                injected = bool(flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED))
                is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("KEY vk=0x%02X %-12s %s injected=%s down=%s",
                              vk, name_of(vk), "DOWN" if is_down else "UP",
                              injected, sorted(self.down))
                if self.probe:
                    if is_down and self.probe_cb:
                        self.probe_cb(vk)
                elif (not injected) or (not self.filter_injected):
                    if is_down:
                        if self._on_down(vk):
                            return 1
                    elif is_up:
                        if self._on_up(vk):
                            return 1
        except Exception:                                        # noqa: BLE001
            log.exception("钩子回调异常")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_down(self, vk: int) -> bool:
        if vk in self.down:          # 系统自动重复（按住不放）
            if vk in self.swallow_vk:
                return True
            # repeat=true 的热键允许按住连发
            for hk in self.hotkeys:
                if hk.enabled and hk.repeat and hk.matches(self.tags):
                    self._dispatch(hk, "repeat")
            return False
        self.down.add(vk)
        self.tags |= tags_of(vk)
        log.debug("DOWN vk=0x%02X %s tags=%s", vk, name_of(vk), sorted(self.tags))

        # 新的按下周期开始：复位「本键参与的热键」的已触发标记。
        # 抬起触发(trigger=up)与长按触发(trigger=long)都靠这个标记去重，
        # 复位必须发生在 down 阶段，不能在 up 阶段顺手复位（那样等于没去重，
        # 遇到重复的 UP 事件会连续触发）。
        for hk in self.hotkeys:
            if vk in hk._vks and not hk.repeat:
                hk._fired = False

        swallowed = False
        for hk in self.hotkeys:
            if not hk.enabled or not hk.matches(self.tags):
                continue
            if hk.swallow:
                self.swallow_vk |= set(self.down)
                swallowed = True
            if hk.trigger == "long":
                if hk._timer is None and not hk._fired:
                    hk._timer = threading.Timer(hk.longpress_ms / 1000.0,
                                                self._long_fire, args=(hk, vk))
                    hk._timer.daemon = True
                    hk._timer.start()
            elif hk.trigger == "down":
                self._fire(hk, "down")
        return swallowed

    def _on_up(self, vk: int) -> bool:
        was_swallow = vk in self.swallow_vk
        if vk not in self.down:
            return was_swallow          # 非本进程记录的按下（例如启动前就按着），不处理
        self.down.discard(vk)
        self.swallow_vk.discard(vk)
        self.tags = set()
        for v in self.down:
            self.tags |= tags_of(v)

        for hk in self.hotkeys:
            if not hk.enabled:
                continue
            # 只有「本热键用到的键」抬起才取消它的长按计时器。
            # 早先写成无条件 cancel，导致任意无关键（例如真实用户敲的 E/Q/W）
            # 一抬起就把长按计时器掐掉 —— 长按触发会随机失效。
            if hk._timer is not None and vk in hk._vks:
                hk._timer.cancel()
                hk._timer = None
            if hk.trigger == "up" and not hk._fired and hk.matches(self.tags | tags_of(vk)):
                self._dispatch(hk, "up")
            if vk in hk._vks:
                hk._fired = False
        if not self.down:
            self.swallow_vk.clear()
        return was_swallow

    def _long_fire(self, hk: Hotkey, vk: int) -> None:
        if vk in self.down:
            self._fire(hk, "long")

    # -- 生命周期 -----------------------------------------------------

    def install(self) -> None:
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                              kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            err = ctypes.get_last_error()
            raise OSError("SetWindowsHookExW 失败，Win32 错误码 %d" % err)
        log.info("键盘钩子已安装 (句柄 %s)", self._hook)

    def uninstall(self) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def loop(self) -> None:
        """消息循环，必须跑在装了钩子的那个线程。"""
        self.ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)


# ---------------------------------------------------------------- HTTP 触发

_WEBUI = None


def get_webui():
    """当前 Web 控制台实例（没有则为 None）。在钩子/HTTP 线程里调用，只读全局引用。"""
    return _WEBUI


def make_http_handler(get_engine):
    class Handler(BaseHTTPRequestHandler):
        server_version = APP_NAME + "/" + APP_VER

        def log_message(self, fmt, *args):                       # noqa: A003
            log.debug("http %s", fmt % args)

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                                        # noqa: N802
            ui = get_webui()
            if ui is not None and ui.handle(self):
                return
            u = urllib.parse.urlparse(self.path)
            path = u.path.strip("/")
            if path in ("", "ping", "health"):
                self._send(200, {"ok": True, "app": APP_NAME, "ver": APP_VER})
                return
            if not path.startswith("t/"):
                self._send(404, {"ok": False, "error": "use /t/<hotkey-id>"})
                return
            hid = urllib.parse.unquote(path[2:])
            eng = get_engine()
            if eng is None:
                self._send(503, {"ok": False, "error": "engine not ready"})
                return
            for hk in eng.hotkeys:
                if hk.id == hid:
                    eng.pool.submit(eng.runner.run, hk.action,
                                    {"id": hk.id, "phase": "http", "at": time.time()})
                    self._send(200, {"ok": True, "id": hid, "name": hk.name})
                    return
            self._send(404, {"ok": False, "error": "unknown hotkey id", "id": hid})

        def do_OPTIONS(self):                                    # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):                                       # noqa: N802
            ui = get_webui()
            if ui is not None and ui.handle(self):
                return
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:                                    # noqa: BLE001
                self._send(400, {"ok": False, "error": "bad json"})
                return
            eng = get_engine()
            if eng is None:
                self._send(503, {"ok": False, "error": "engine not ready"})
                return
            eng.pool.submit(eng.runner.run, body,
                            {"id": "http-inline", "phase": "http", "at": time.time()})
            self._send(200, {"ok": True})

    return Handler


# ---------------------------------------------------------------- 配置

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_hotkeys(cfg: dict) -> list[Hotkey]:
    out = []
    for raw in (cfg.get("hotkeys") or []):
        if not raw.get("enabled", True):
            log.info("跳过已停用热键: %s", raw.get("id"))
            continue
        try:
            out.append(Hotkey(raw))
        except Exception as e:                                   # noqa: BLE001
            log.error("热键配置无效: %s (%s)", raw.get("id"), e)
    return out


def file_digest(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except Exception:                                            # noqa: BLE001
        return ""


def webui_url_path(base_dir: str, cfg_path: str = "") -> str:
    """控制台地址文件。按配置文件名分文件，避免多个实例互相覆盖对方的端口。

    config.json     → runtime/webui.url
    _prodtest.json  → runtime/webui._prodtest.url
    """
    stem = "config"
    if cfg_path:
        stem = os.path.splitext(os.path.basename(cfg_path))[0].lstrip("_") or "config"
    name = "webui.url" if stem in ("config", "keybridge") else "webui.%s.url" % stem
    return os.path.join(base_dir, "runtime", name)


def read_webui_url(base_dir: str, host: str = "127.0.0.1", cfg_path: str = "") -> str:
    """读回 serve() 写下的控制台地址；只在地址指向本机时返回，避免被文件里的脏值带跑。"""
    try:
        with open(webui_url_path(base_dir, cfg_path), "r", encoding="utf-8") as f:
            url = f.read().strip()
        if url.startswith("http://%s:" % host) or url.startswith("http://127.0.0.1:"):
            return url
    except Exception:                                            # noqa: BLE001
        pass
    return ""


# ---------------------------------------------------------------- 单实例

_mutex = None


def single_instance(tag: str) -> bool:
    global _mutex
    kernel32.CreateMutexW.restype = wt.HANDLE
    _mutex = kernel32.CreateMutexW(None, False, "Global\\" + APP_NAME + "_" + tag)
    return ctypes.get_last_error() != 183     # ERROR_ALREADY_EXISTS


# ---------------------------------------------------------------- 模式

def mode_probe(hotkeys: list[Hotkey]) -> int:
    eng = HookEngine(hotkeys, ActionRunner(BASE_DIR))
    eng.probe = True
    seen = []
    lock = threading.Lock()

    def on_key(vk: int) -> None:
        n = name_of(vk)
        with lock:
            if n not in seen:
                seen.append(n)
        print("  VK 0x%02X  %-14s  scan=%d" % (vk, n, 0), flush=True)

    eng.probe_cb = on_key
    eng.install()
    print("按键探测模式：按任意键看键名，Ctrl+C 结束。", flush=True)
    try:
        eng.loop()
    except KeyboardInterrupt:
        pass
    finally:
        eng.uninstall()
    return 0


def mode_test(cfg: dict, hid: str, base_dir: str) -> int:
    runner = ActionRunner(base_dir)
    for hk in build_hotkeys(cfg):
        if hk.id == hid:
            print("执行 %s -> %s" % (hk.id, json.dumps(hk.action, ensure_ascii=False)))
            runner.run(hk.action, {"id": hk.id, "phase": "test"})
            return 0
    print("找不到热键 id: %s" % hid)
    return 1


def mode_list(cfg: dict) -> int:
    print("%-22s %-16s %-8s %-8s %s" % ("ID", "按键", "触发", "吞键", "动作"))
    print("-" * 92)
    for hk in build_hotkeys(cfg):
        a = hk.action
        desc = "%s %s" % (a.get("type", "?"),
                          a.get("program") or a.get("command") or a.get("url")
                          or a.get("file") or "")
        print("%-22s %-16s %-8s %-8s %s" % (hk.id, "+".join(hk.keys), hk.trigger,
                                            "是" if hk.swallow else "否", desc.strip()))
    return 0


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(prog=APP_NAME, description="按键桥：按键 → 进程/命令/代码")
    ap.add_argument("-c", "--config", default=os.path.join(BASE_DIR, "config.json"))
    ap.add_argument("--test", metavar="HOTKEY_ID", help="不装钩子，直接跑一次指定动作")
    ap.add_argument("--list", action="store_true", help="列出所有热键")
    ap.add_argument("--probe", action="store_true", help="按键探测模式")
    ap.add_argument("--allow-injected", action="store_true",
                    help="同时响应其他程序模拟出来的按键（默认忽略，防止自己触发自己）")
    ap.add_argument("--web", action="store_true", help="启动后自动打开浏览器控制台")
    ap.add_argument("--no-web", action="store_true", help="本次不启动 Web 控制台")
    args = ap.parse_args()

    cfg_path = os.path.abspath(args.config)
    if not os.path.exists(cfg_path):
        print("找不到配置文件: %s" % cfg_path, file=sys.stderr)
        return 2
    base_dir = os.path.dirname(cfg_path)
    cfg = load_config(cfg_path)
    setup_log(cfg, base_dir)

    if args.list:
        return mode_list(cfg)
    if args.test:
        return mode_test(cfg, args.test, base_dir)
    if args.probe:
        return mode_probe([])

    log.info("=" * 62)
    log.info("%s v%s 启动，配置: %s", APP_NAME, APP_VER, cfg_path)

    if not single_instance(cfg_path):
        log.error("已有实例在运行（同一配置）。退出。")
        return 3

    state: dict = {"engine": None}
    runner = ActionRunner(base_dir)
    engine = HookEngine(build_hotkeys(cfg), runner,
                        filter_injected=not args.allow_injected)
    state["engine"] = engine

    engine.install()

    # ---- 配置热重载 ----
    digest = {"v": file_digest(cfg_path)}
    with open(cfg_path, "r", encoding="utf-8") as _f:
        raw_cfg = {"v": json.load(_f)}          # 保留用户写的注释/未知字段，写回时不丢

    def apply_config(new_cfg: dict) -> tuple[bool, str, int, list]:
        """把一份配置变成生效的热键。返回 (ok, msg, 生效条数, 警告列表)。"""
        warnings: list[str] = []
        try:
            new_hk = build_hotkeys(new_cfg)
        except Exception as e:                                   # noqa: BLE001
            return False, "解析失败: %s" % e, 0, warnings
        # 构造失败的热键会在 build_hotkeys 里记 error，这里把它们数出来提醒前端
        wanted = [h for h in (new_cfg.get("hotkeys") or []) if h.get("enabled", True)]
        if len(new_hk) < len(wanted):
            bad = [str(h.get("id") or "?") for h in wanted
                   if str(h.get("id") or "?") not in {x.id for x in new_hk}]
            warnings.append("这些热键没生效（配置有问题）: %s" % ", ".join(bad))
        engine.hotkeys = new_hk
        raw_cfg["v"] = new_cfg
        return True, "", len(new_hk), warnings

    def do_reload() -> tuple[bool, str, int, list]:
        """从磁盘重新读配置（Web 控制台保存后就调它）。"""
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                new_cfg = json.load(f)
        except Exception as e:                                   # noqa: BLE001
            return False, "读取配置文件失败: %s" % e, 0, []
        digest["v"] = file_digest(cfg_path)
        ok, msg, n, warns = apply_config(new_cfg)
        if ok:
            log.info("配置已重新加载，%d 条热键生效", n)
        return ok, msg, n, warns

    def watcher() -> None:
        while True:
            time.sleep(2.0)
            d = file_digest(cfg_path)
            if d and d != digest["v"]:
                digest["v"] = d
                try:
                    new_cfg = load_config(cfg_path)
                    ok, msg, n, _ = apply_config(new_cfg)
                    if ok:
                        log.info("配置已热重载，%d 条热键生效", n)
                    else:
                        log.error("热重载失败（保留旧配置）: %s", msg)
                except Exception as e:                           # noqa: BLE001
                    log.error("热重载失败（保留旧配置）: %s", e)

    threading.Thread(target=watcher, daemon=True, name="cfg-watch").start()

    # ---- HTTP 触发 + Web 控制台 ----
    ht = cfg.get("httpTrigger") or {}
    web_cfg = cfg.get("webui") or {}
    want_web = (not args.no_web) and web_cfg.get("enabled", True)
    if ht.get("enabled") or want_web:
        host = ht.get("host", "127.0.0.1") if ht.get("enabled") else "127.0.0.1"
        port = int(ht.get("port", 8765)) if ht.get("enabled") else int(web_cfg.get("port", 8765))

        if want_web:
            try:
                import webui as _webui
                global _WEBUI

                def _state() -> dict:
                    return {"config": raw_cfg["v"],
                            "activeCount": len(engine.hotkeys),
                            "injected": not engine.filter_injected}

                def _run(action: dict) -> None:
                    engine.pool.submit(runner.run, action,
                                       {"id": "webui-test", "phase": "webui",
                                        "at": time.time()})

                def _logpath() -> str:
                    lf = cfg.get("logFile", "")
                    if not lf:
                        return ""
                    return lf if os.path.isabs(lf) else os.path.join(base_dir, lf)

                _WEBUI = _webui.WebUI(cfg_path, _webui.make_token(), _state,
                                      do_reload, _run, _logpath,
                                      key_names=_webui.key_names_from(sys.modules[__name__]))
                log.info("Web 控制台已启用")
            except Exception as e:                               # noqa: BLE001
                log.warning("Web 控制台不可用: %s", e)

        def bind_server() -> tuple:
            """绑定端口。端口被占用/被保留时自动往后试几个，避免被别的服务挡住就整个控制台没了。"""
            last = None
            for i in range(PORT_TRY):
                p = port + i
                try:
                    s = ThreadingHTTPServer((host, p),
                                            make_http_handler(lambda: state["engine"]))
                    s.daemon_threads = True
                    return s, p, None
                except OSError as e:                             # noqa: PERF203
                    last = e
                    if getattr(e, "winerror", None) in (10013, 10048) or e.errno in (13, 98):
                        log.warning("端口 %d 不可用（%s），换 %d 试试", p, e, p + 1)
                        continue
                    break
            return None, port, last

        def write_url(p: int) -> None:
            """把控制台地址落到文件，方便 console.cmd 之类的启动器直接打开。"""
            try:
                rt = os.path.join(base_dir, "runtime")
                os.makedirs(rt, exist_ok=True)
                path = webui_url_path(base_dir, cfg_path)
                if _WEBUI is None:
                    return
                with open(path, "w", encoding="utf-8") as f:
                    f.write("http://%s:%d/?t=%s" % (host, p, _WEBUI.token))
            except Exception:                                    # noqa: BLE001
                pass

        def serve() -> None:
            srv, real_port, err = bind_server()
            if srv is None:
                log.error("HTTP 服务启动失败 (%s:%d~%d): %s",
                          host, port, port + PORT_TRY - 1, err)
                log.error("控制台和 HTTP 触发都不可用。可用 netstat -ano | findstr :%d "
                          "看看是谁占了端口，或在配置里把 webui.port 换成别的值。", port)
                return
            log.info("HTTP 触发服务: http://%s:%d/t/<hotkey-id>", host, real_port)
            if _WEBUI is not None:
                log.info("控制台地址:      http://%s:%d/?t=%s", host, real_port, _WEBUI.token)
                write_url(real_port)
            try:
                srv.serve_forever()
            except Exception as e:                               # noqa: BLE001
                log.warning("HTTP 服务异常退出: %s", e)

        http_thread = threading.Thread(target=serve, daemon=True, name="http-trigger")
        http_thread.start()
        # 等一小会儿让端口绑定完成，这样 webui.url 里写的是真实端口
        http_thread.join(1.5)

        if want_web and _WEBUI is not None and (args.web or web_cfg.get("openBrowser")):
            try:
                import webui as _webui
                _webui.open_browser_later(read_webui_url(base_dir, host, cfg_path) or
                                          "http://%s:%d/?t=%s" % (host, port, _WEBUI.token),
                                          delay=1.2)
            except Exception:                                    # noqa: BLE001
                pass

    log.info("已加载 %d 条热键:", len(engine.hotkeys))
    for hk in engine.hotkeys:
        log.info("   %-22s %-16s %s", hk.id, "+".join(hk.keys), hk.trigger)

    try:
        engine.loop()
    except KeyboardInterrupt:
        log.info("收到中断，退出。")
    finally:
        engine.uninstall()
    return 0


if __name__ == "__main__":
    sys.exit(main())
