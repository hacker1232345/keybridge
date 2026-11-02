# -*- coding: utf-8 -*-
"""生成 console.cmd（GBK 编码）。

cmd.exe 读取 .cmd 文件时按系统 ANSI 代码页（简体中文 = GBK/936）解码。
把带中文的批处理存成 UTF-8 会被逐字节拆坏，表现为：
  '可能是上一次运行留下的旧端口，先从日志里取最新的地址' 不是内部或外部命令
所以这里直接以 GBK 落盘，运行时不依赖 chcp。

另外两个坑（都实测踩过）：
  1. for /f 反引号里的 `|` 必须转义成 `^|`，而转义后 PowerShell 反而收到字面 `^`。
     改成无管道的 `@(Select-String ...)[-1]`。
  2. `timeout /t` 在 PATH 里有 GNU coreutils 时会被解析成 GNU timeout。
     改成 `ping -n N 127.0.0.1 >nul` 做延时，任何环境都稳。

用法：
  python _mkconsole.py                     生成 console.cmd（源码版，调 pythonw keybridge.py）
  python _mkconsole.py --exe keybridge.exe 生成 console.cmd（打包版，调同目录 exe）
  python _mkconsole.py --out D:\\x\\console.cmd
"""
import argparse
import os

HEAD = r'''@echo off
setlocal enabledelayedexpansion
title keybridge 控制台
cd /d "%~dp0"

rem ============================================================
rem  打开 keybridge 的 Web 控制台（改热键用）
rem  如果 keybridge 没在跑，就顺手启动它。
rem
rem  三级回退：
rem    1. 读 runtime\webui.url 探活
rem    2. 不活就去 logs\keybridge.log 抓最后一条「控制台地址: ...」
rem    3. 还不行才启动新进程，然后轮询等待
rem ============================================================

set "URLFILE=%~dp0runtime\webui.url"
set "LOGFILE=%~dp0logs\keybridge.log"
'''

MIDDLE = r'''
rem ---- 第 1 级：现有地址文件 ----
set "URL="
if exist "%URLFILE%" set /p URL=<"%URLFILE%"
call :probe
if "%ALIVE%"=="200" goto open

rem ---- 第 2 级：日志里最后一条控制台地址 ----
set "URL="
call :fromlog
call :probe
if "%ALIVE%"=="200" goto open

rem ---- 第 3 级：启动，然后最多等 20 秒 ----
echo keybridge 没在运行，正在启动...
if not exist "logs" mkdir "logs"
{launch}
set /a TRY=0
:wait
call :delay
set /a TRY+=1

set "URL="
if exist "%URLFILE%" set /p URL=<"%URLFILE%"
call :probe
if "%ALIVE%"=="200" goto open

if %TRY% GEQ 10 goto giveup
echo   等待中... (%TRY%/10)
goto wait

:giveup
set "URL="
call :fromlog
call :probe
if "%ALIVE%"=="200" goto open

echo.
echo [没能拿到控制台地址]
echo  1. 确认 config.json 里有  "webui": { "enabled": true }
echo  2. 打开日志 %LOGFILE%
echo     里面应该有一行：控制台地址: http://127.0.0.1:8766/?t=xxxx
echo  3. 把那个地址复制到浏览器打开。
echo.
call :hold
exit /b 1
'''

TAIL = r'''
:open
if not defined URL goto giveup
echo 打开：%URL%
start "" "%URL%"
exit /b 0

rem ------------------------------------------------------------
rem 约 2 秒延时（不依赖 timeout.exe）
:delay
ping -n 3 127.0.0.1 >nul 2>nul
goto :eof

rem ------------------------------------------------------------
rem 双击运行时停住等人看；被脚本调用时直接返回
:hold
if defined NO_PAUSE goto :eof
echo 按任意键关闭...
pause >nul
goto :eof

rem ------------------------------------------------------------
rem 探测 URL 是否活着，结果放进 ALIVE
:probe
set "ALIVE="
if not defined URL goto :eof
for /f %%A in ('powershell -NoProfile -Command "try{(Invoke-WebRequest -Uri '%URL%' -TimeoutSec 2 -UseBasicParsing).StatusCode}catch{'0'}"') do set "ALIVE=%%A"
goto :eof

rem ------------------------------------------------------------
rem 从日志尾部取最后一条「控制台地址: ...」，放进 URL
rem 注意：这里不能用管道，for /f 反引号里的 | 转义后 PowerShell 会收到字面 ^
:fromlog
if not exist "%LOGFILE%" goto :eof
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "$a = @(Select-String -Path '%LOGFILE%' -Pattern 'http://127.0.0.1:' -Encoding UTF8); if ($a.Count -gt 0) { $line = $a[$a.Count-1].Line; if ($line -match '(http://[^ ]+)') { $Matches[1] } }"`) do set "URL=%%L"
goto :eof
'''

# 打包版：直接运行同目录的 exe
LAUNCH_EXE = 'start "" "%~dp0{exe}" -c "%~dp0config.json"'

# 源码版：优先 pythonw（无窗口），退化到 python
LAUNCH_PY = (
    'where pythonw >nul 2>nul\n'
    'if errorlevel 1 (\n'
    '    start "" /b python "%~dp0{script}" -c "%~dp0config.json"\n'
    ') else (\n'
    '    start "" /b pythonw "%~dp0{script}" -c "%~dp0config.json"\n'
    ')'
)


def build(exe=None, script="keybridge.py"):
    launch = LAUNCH_EXE.format(exe=exe) if exe else LAUNCH_PY.format(script=script)
    # 注意：MIDDLE 里有 JSON 大括号（"webui": { "enabled": true }），
    # 用 str.format 会把它当占位符 → KeyError。这里用 replace 单点替换。
    return HEAD + MIDDLE.replace("{launch}", launch) + TAIL


def main():
    ap = argparse.ArgumentParser(description="生成 GBK 编码的 console.cmd")
    ap.add_argument("--exe", help="打包版：要启动的 exe 文件名（与 console.cmd 同目录）")
    ap.add_argument("--script", default="keybridge.py", help="源码版：要启动的 py 文件名")
    ap.add_argument("--out", help="输出路径，默认同目录 console.cmd")
    a = ap.parse_args()

    text = build(exe=a.exe, script=a.script)
    base = os.path.dirname(os.path.abspath(__file__))
    out = a.out or os.path.join(base, "console.cmd")
    with open(out, "w", encoding="gbk", newline="\r\n") as f:
        f.write(text)
    print("wrote", out, os.path.getsize(out), "bytes (GBK)")


if __name__ == "__main__":
    main()