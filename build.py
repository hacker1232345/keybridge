# -*- coding: utf-8 -*-
"""打 Release 用的 zip。

两个产物：
  1. keybridge-<ver>-win64.zip          免安装成品（PyInstaller 单文件 exe + 配置 + 启动器）
  2. keybridge-<ver>-source.zip         源码包（就是仓库内容，方便不用 git 的人）

用法：
  python build.py              # 两个都打
  python build.py --portable   # 只打成品
  python build.py --source     # 只打源码包
  python build.py --no-exe     # 成品里不放 exe（只放 .py + 依赖说明）
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
BUILD = os.path.join(HERE, "build")
APP = "keybridge"

# 仓库里不进 zip 的东西
EXCLUDE_DIRS = {"dist", "build", ".git", "__pycache__", "logs", "runtime", ".venv", "venv", ".idea", ".vscode"}
EXCLUDE_FILES = {".gitignore", "config.json", "config.json.bak"}


def version():
    """从 src/keybridge.py 里取版本号。

    keybridge.py 里是 `APP_VER = "1.0"` + `__version__ = APP_VER`，
    所以要顺着名字解析一遍，不能只抓 `__version__` 那一行。
    """
    p = os.path.join(HERE, "src", "keybridge.py")
    vals = {}
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'^(APP_VER|__version__)\s*=\s*(.+?)\s*$', line)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip("\"'")
    v = vals.get("__version__") or vals.get("APP_VER") or "0.0.0"
    if v in vals:                      # __version__ = APP_VER 这种间接引用
        v = vals[v]
    return v


def walk_source(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        for fn in files:
            if fn in EXCLUDE_FILES or fn.endswith((".pyc", ".pyo", ".zip")):
                continue
            if fn.endswith(".log"):
                continue
            yield dirpath, rel_dir, fn


# ---------------------------------------------------------------- 成品

def make_portable(ver, with_exe=True):
    stage = os.path.join(BUILD, "portable")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    if with_exe:
        _run_pyinstaller()
        exe = os.path.join(DIST, APP + ".exe")
        if not os.path.isfile(exe):
            raise SystemExit("PyInstaller 没产出 exe: " + exe)
        shutil.copy2(exe, os.path.join(stage, APP + ".exe"))
        launch_name = APP + ".exe"
    else:
        shutil.copytree(os.path.join(HERE, "src"), os.path.join(stage, "src"))
        launch_name = None

    # 配置 + 示例
    shutil.copy2(os.path.join(HERE, "examples", "config.minimal.json"),
                 os.path.join(stage, "config.json"))
    os.makedirs(os.path.join(stage, "examples"), exist_ok=True)
    for fn in ("config.minimal.json", "config.advanced.json"):
        shutil.copy2(os.path.join(HERE, "examples", fn), os.path.join(stage, "examples", fn))

    # 示例脚本
    shutil.copytree(os.path.join(HERE, "scripts"), os.path.join(stage, "scripts"))

    # 文档
    os.makedirs(os.path.join(stage, "docs"), exist_ok=True)
    shutil.copy2(os.path.join(HERE, "README.md"), os.path.join(stage, "README.md"))
    shutil.copy2(os.path.join(HERE, "LICENSE"), os.path.join(stage, "LICENSE"))
    for fn in ("USAGE.md", "PROJECT-NOTES.md", "aula-keys.txt"):
        shutil.copy2(os.path.join(HERE, "docs", fn), os.path.join(stage, "docs", fn))

    # 启动器：调 _mkconsole 生成 GBK 的 console.cmd
    mk = os.path.join(HERE, "src", "_mkconsole.py")
    out = os.path.join(stage, "console.cmd")
    cmd = [sys.executable, mk, "--out", out]
    if launch_name:
        cmd += ["--exe", launch_name]
    else:
        cmd += ["--script", os.path.join("src", APP + ".py")]
    subprocess.check_call(cmd)

    # start.cmd / stop.cmd（也走 GBK）
    _write_cmd(os.path.join(stage, "start.cmd"), _start_cmd_text(launch_name))
    _write_cmd(os.path.join(stage, "stop.cmd"), _stop_cmd_text())

    # 版本说明
    with open(os.path.join(stage, "VERSION.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("keybridge %s\n" % ver)
        f.write("https://github.com/hacker1232345/keybridge/releases\n\n")
        f.write("双击 console.cmd 打开控制台。\n")

    zpath = os.path.join(DIST, "%s-%s-win64.zip" % (APP, ver))
    _zip_dir(stage, zpath, prefix="%s-%s/" % (APP, ver))
    return zpath


def _run_pyinstaller():
    os.makedirs(DIST, exist_ok=True)
    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--noconfirm", "--clean",
        "--name", APP,
        "--distpath", DIST,
        "--workpath", os.path.join(BUILD, "pyi"),
        "--specpath", BUILD,
        "--console",
        "--collect-submodules", "encodings",
        os.path.join(HERE, "src", "keybridge.py"),
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    subprocess.check_call(args, env=env)


def _start_cmd_text(launch_name):
    if launch_name:
        # 注意 %~dp0 里的 %~ 会被 % 格式化吃掉，改用 replace
        run = 'start "" /b "%~dp0{exe}" -c "%~dp0config.json"'.replace("{exe}", launch_name)
    else:
        run = ('where pythonw >nul 2>nul\n'
               'if errorlevel 1 (\n'
               '    start "" /b python "%~dp0src\\keybridge.py" -c "%~dp0config.json"\n'
               ') else (\n'
               '    start "" /b pythonw "%~dp0src\\keybridge.py" -c "%~dp0config.json"\n'
               ')')
    return r'''@echo off
chcp 65001 >nul
title keybridge
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
echo 正在启动 keybridge ...
''' + run + r'''
echo.
echo 日志:  type "%~dp0logs\keybridge.log"
echo 控制台: 双击 console.cmd
echo 停止:  stop.cmd
ping -n 4 127.0.0.1 >nul
'''


def _stop_cmd_text():
    return r'''@echo off
chcp 65001 >nul
title keybridge - 停止
echo 正在停止 keybridge ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='keybridge.exe' or Name='pythonw.exe' or Name='python.exe'\" | Where-Object { $_.CommandLine -like '*keybridge*' } | ForEach-Object { Write-Host ('  kill ' + $_.ProcessId + '  ' + $_.Name); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo 完成。
ping -n 3 127.0.0.1 >nul
'''


# ---------------------------------------------------------------- 源码包

def make_source(ver):
    zpath = os.path.join(DIST, "%s-%s-source.zip" % (APP, ver))
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, rel_dir, fn in walk_source(HERE):
            src = os.path.join(dirpath, fn)
            rel = os.path.normpath(os.path.join(rel_dir, fn)).replace("\\", "/")
            z.write(src, "%s-%s/%s" % (APP, ver, rel))
    return zpath


def _zip_dir(stage, zpath, prefix):
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(stage):
            for fn in files:
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, stage).replace("\\", "/")
                z.write(src, prefix + rel)


def _write_cmd(path, text):
    with open(path, "w", encoding="gbk", newline="\r\n") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portable", action="store_true", help="只打成品 zip")
    ap.add_argument("--source", action="store_true", help="只打源码 zip")
    ap.add_argument("--no-exe", action="store_true", help="成品里不放 exe")
    a = ap.parse_args()

    ver = version()
    os.makedirs(DIST, exist_ok=True)
    print("版本:", ver)

    do_por = a.portable or not (a.portable or a.source)
    do_src = a.source or not (a.portable or a.source)

    if do_por:
        p = make_portable(ver, with_exe=not a.no_exe)
        print("成品:", p, "%.1f MB" % (os.path.getsize(p) / 1048576.0))
    if do_src:
        p = make_source(ver)
        print("源码:", p, "%.1f KB" % (os.path.getsize(p) / 1024.0))


if __name__ == "__main__":
    main()