# 完整使用手册

> 这是 `README.md` 的详细版。想快速上手看 README；想搞清每个字段、每个坑，看这里。

---

## 目录

1. [它在解决什么问题](#1-它在解决什么问题)
2. [驱动侧怎么配](#2-驱动侧怎么配)
3. [按键到底怎么选](#3-按键到底怎么选)
4. [九种动作全表](#4-九种动作全表)
5. [触发方式：down / up / long](#5-触发方式down--up--long)
6. [键名怎么写](#6-键名怎么写)
7. [网页控制台](#7-网页控制台)
8. [HTTP 触发通道](#8-http-触发通道)
9. [命令行参数](#9-命令行参数)
10. [开机自启](#10-开机自启)
11. [FAQ 与踩坑记录](#11-faq-与踩坑记录)
12. [逆向溯源](#12-逆向溯源)

---

## 1. 它在解决什么问题

大部分键盘驱动（狼蛛 Aula Hub、罗技 G HUB、雷蛇 Synapse、各种杂牌驱动）的
「自定义按键」都有天花板：

| 能做到 | 做不到 |
|---|---|
| 把 A 键映射成 B 键 | 跑一个 exe |
| 组合键（Ctrl+C） | 执行一条命令 |
| 宏（一串按键序列） | 运行一段代码 |
| 打开一个网址（部分驱动） | 带参数启动、指定工作目录 |

突破天花板有两条路：

**路线 A：改驱动** —— 逆向固件协议，往里塞自定义动作。
问题是固件只认 16 位 HID 键码，塞不下一整条命令；而且升级固件就白干。

**路线 B：加一个本机代理** —— 让驱动继续做它擅长的事（发一个罕见的键），
代理监听这个键，然后执行任意动作。**这就是 keybridge。**

```
   ┌──────────┐  ① 把某键改成 ScrollLock   ┌──────────────┐
   │ 物理键盘  │ ────────────────────────► │ 键盘驱动固件  │
   └──────────┘                           └──────┬───────┘
                                                 │ ② 固件发出 ScrollLock
                                                 ▼
                                          ┌─────────────┐
                                          │  Windows    │
                                          └──────┬──────┘
                                                 │ ③ WH_KEYBOARD_LL 钩子
                                                 ▼
                                          ┌─────────────┐
                                          │  keybridge  │ ④ 匹配热键
                                          └──────┬──────┘
                                                 │ ⑤ 执行动作
                                                 ▼
                       跑 exe / 跑命令 / 跑代码 / 开网址 / 模拟按键 / …
```

**驱动负责「哪个键」，keybridge 负责「按下之后干什么」。**

---

## 2. 驱动侧怎么配

以狼蛛 Aula Hub 为例（其他驱动思路相同）：

1. 打开驱动 → 键盘设置 → **改键 / 自定义按键**
2. 选一颗平时**不会按**的键（`ScrollLock` / `Pause` / `Insert`）
3. 输出设成**同一个键**（有些驱动叫「普通键输出」，会让你在键盘上按一下来指定）
4. 保存到设备（有的要点「应用」或「下载到键盘」）

⚠️ 不建议改 `CapsLock`、`Win`、`Ctrl`、`Alt` 这类常用键。

⚠️ 键盘的「Win 锁」/「游戏模式」可能把你的目标键锁掉，先确认。

---

## 3. 按键到底怎么选

最容易踩坑的一步：**不是所有键都能在驱动里选到。**

### 3.1 查自己的驱动支持哪些键

```bash
python src/_aula_keys.py
```

输出 114 条对照表（驱动里的名字 / HID 码 / keybridge 写法）+ 推荐键列表。
仓库自带 `data/aula-keys.json`（已 dump 好的结果），**不用装驱动也能查**。
想解析自己的安装目录，设环境变量 `AULA_RE` 指向 asar 解包目录。

### 3.2 狼蛛 Aula Hub 的实测结论

| 结论 | 证据 |
|---|---|
| **不含 F13–F24** | 两张表都是 `F1`(0x3a) 到 `F12`(0x45) 结束，`grep F13` 零命中 |
| 不含多媒体键 | 表里没有 `MediaPlayPause`、`VolumeUp` 等 |
| 有这些罕见键 | `PrintScreen 0x46`、`ScrollLock 0x47`、`Pause 0x48`、`Insert 0x49`、`Home 0x4a`、`End 0x4d`、`NumLock 0x53`、`ContextMenu 0x01`、`Apps 0x65`、`IntlYen 0x89`、`IntlRo 0x87`、`Convert 0x8a`、`NonConvert 0x8b`、`KanaMode 0x88`、`IntlBackslash 0x64`、`Hangul 0x90`、`HangulHanja 0x91` |

> **注意区分两张表**：`svncPnbJ.js` 里有一套 `funcF13:2100 … funcF24:2111` 的码表，
> 那是**鼠标侧键 / 宏动作**用的功能码，**不是**「普通键输出能选哪些键」。
> 协议层认识某个码 ≠ 用户在 UI 里能选到它。

### 3.3 推荐触发键

| 键 | 冲突 | 备注 |
|---|---|---|
| `ScrollLock` | 极低 | **首选**，绝大多数软件不认它 |
| `Pause` | 极低 | 同上，个别全屏游戏会用到 |
| `Insert` | 极低 | 会切换插入/覆盖模式，建议配 `swallow: true` |
| `PrtSc` | 低 | 改掉后系统截图键归你 |
| `ContextMenu` / `Apps` | 极低 | 右键菜单键 |
| `NumLock` | 中 | 会影响小键盘数字输入 |
| `F1`–`F12` | **高** | 游戏/IDE/浏览器都可能占用，慎用 |
| `F13`–`F24` | — | Aula 里选不到；只有 AHK、宏键盘、部分专业驱动能发 |

---

## 4. 九种动作全表

每个热键的 `action` 是对象，`type` 决定类型。

### 4.1 `run` — 启动程序

```json
{
  "type": "run",
  "program": "notepad.exe",
  "args": ["D:\\notes.txt"],
  "cwd": "D:\\",
  "hidden": true,
  "console": false
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `program` | ✅ | 见下表 |
| `args` | | 参数数组，也可单个字符串 |
| `cwd` | | 工作目录，相对路径按配置文件目录解析 |
| `hidden` | | 默认 `true`，无窗口启动 |
| `console` | | `true` 时开新控制台（想看输出用） |

`program` 可以是：

| 写法 | 例子 | 说明 |
|---|---|---|
| 裸程序名 | `notepad.exe` | 交给 PATH |
| 绝对路径 exe | `D:\Apps\MyApp\MyApp.exe` | 直接 CreateProcess |
| **`.lnk` 快捷方式** | `D:\桌面\我的应用.lnk` | 自动转 ShellExecute |
| `.bat` / `.cmd` | `D:\scripts\run.bat` | 走 cmd |
| `.url` / 文档 / 目录 | `D:\notes.pdf` | 走 ShellExecute，用默认程序打开 |

**坑 ①：`.lnk` 不是可执行文件。**
`CreateProcess` 会报 `OSError: [WinError 193] %1 不是有效的 Win32 应用程序`。
keybridge 自动捕获并改用 `ShellExecuteW`，日志：

```
启动: ['D:\桌面\我的应用.lnk'] (cwd=, hidden=True, console=False)
CreateProcess 打不开（WinError 193），改用 ShellExecute 打开: D:\桌面\我的应用.lnk
ShellExecute 已打开: D:\桌面\我的应用.lnk
```

**坑 ②：`ELECTRON_RUN_AS_NODE` 会让 Electron 程序变哑巴。**
目标若是 Electron 应用（VS Code 系、各种自研客户端、下载器），而父进程环境里有
`ELECTRON_RUN_AS_NODE=1`，那个应用会退化成纯 Node 解释器：**不开窗口、不报错、退出码 0**。
从外面看就是「点了没反应」。

keybridge 主动剥离这 8 个变量再启动子进程：

```
ELECTRON_RUN_AS_NODE
ELECTRON_NO_ATTACH_CONSOLE
ELECTRON_ENABLE_LOGGING
ELECTRON_ENABLE_STACK_DUMPING
ELECTRON_FORCE_WINDOW_MENU_BAR
ELECTRON_DEFAULT_ERROR_MODE
ELECTRON_OVERRIDE_DIST_PATH
NODE_OPTIONS
```

自检（PowerShell）：`Get-ChildItem Env: | Where-Object Name -like 'ELECTRON*'`

### 4.2 `shell` — 执行命令

```json
{
  "type": "shell",
  "command": "echo hello >> D:\\kb-test.log",
  "shell": "cmd",
  "cwd": "D:\\",
  "hidden": true
}
```

`shell` 可选 `cmd`（默认）/ `powershell` / `pwsh` / `bash`：

- `cmd` → `cmd.exe /d /c <command>`
- `powershell` → `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command <command>`
- `pwsh` → 同上，PowerShell 7
- `bash` → `bash -lc <command>`

### 4.3 `script` — 运行脚本文件

```json
{
  "type": "script",
  "file": "scripts/demo.py",
  "args": ["hello"],
  "interpreter": "python",
  "cwd": "scripts"
}
```

`interpreter` 不填按扩展名推断：

| 扩展名 | 解释器 |
|---|---|
| `.py` | python |
| `.ps1` | powershell |
| `.bat` / `.cmd` | cmd |
| `.js` / `.mjs` | node |
| `.vbs` | wscript |
| `.sh` | bash |

> 打包成 exe 后，`python` 解释器按 `KEYBRIDGE_PYTHON` 环境变量 → PATH 里的
> `python.exe` → `py.exe` 依次查找（打包版里 `sys.executable` 是 exe 自己，不能用）。
> 想让某个 `.py` 用特定解释器，设这个变量。

### 4.4 `code` — 运行内联代码

```json
{
  "type": "code",
  "lang": "python",
  "code": "import datetime; print(datetime.datetime.now())"
}
```

代码按 `sha1(code)[:10]` 命名落到 `runtime/tmp/inline_<hash>.<ext>`，然后当脚本跑。
同样的代码第二次执行复用同一个文件。

`lang` 支持 `python` / `powershell` / `ps` / `bat` / `cmd` / `js` / `node` / `vbs`。

### 4.5 `url` — 打开网址 / 文件 / 目录

```json
{ "type": "url", "url": "https://www.bilibili.com" }
```

绝对路径且存在时也走 ShellExecute（能打开 `.lnk`、文档、目录）。

### 4.6 `keys` — 模拟按键

```json
{ "type": "keys", "keys": ["CTRL", "C"], "delay": 0.03 }
```
```json
{ "type": "keys", "sequence": "你好世界" }
```

`keys` 是同时按下的组合，`sequence` 是逐字符输入。
`delay` 是每步间隔（秒），默认 0.03。

### 4.7 `sequence` — 串行做多件事

```json
{
  "type": "sequence",
  "steps": [
    { "type": "run", "program": "notepad.exe", "waitAfter": 1.5 },
    { "type": "keys", "sequence": "hello" },
    { "type": "keys", "keys": ["ENTER"] }
  ]
}
```

每步可带 `waitAfter`（秒）。

### 4.8 `http` — 发 HTTP 请求

```json
{
  "type": "http",
  "url": "http://127.0.0.1:8080/hook",
  "method": "POST",
  "headers": { "Content-Type": "application/json" },
  "body": "{\"event\":\"key\"}",
  "timeout": 10
}
```

### 4.9 `opendir` — 打开文件夹

```json
{ "type": "opendir", "dir": "D:\\Downloads" }
```

---

## 5. 触发方式：down / up / long

| `trigger` | 何时触发 | 适用 |
|---|---|---|
| `down`（默认） | 按下那一刻 | 绝大多数场景 |
| `up` | 松开那一刻 | 避免「按下就跑」的误触 |
| `long` | 按住超过 `longpress_ms`（默认 600） | 「长按才触发」，短按保持原功能 |

配合两个开关：

| 字段 | 默认 | 说明 |
|---|---|---|
| `swallow` | `false` | `true` 时吞掉这个键，系统收不到。用于 `ScrollLock` 这种「不想真的切滚动锁」的场景 |
| `repeat` | `false` | `true` 时按住会连发；`false` 时按住只触发一次 |

`long` 的注意点：只有**这个热键用到的键**抬起才会取消计时器，
长按期间敲别的键不影响。

---

## 6. 键名怎么写

大小写不敏感，`_` 和 `-` 会被忽略。以下写法全部等价：

| 目标 | 可写 |
|---|---|
| 左 Ctrl | `LCTRL` / `Ctrl` / `Control` / `LeftControl` |
| 右 Ctrl | `RCTRL` / `RightCtrl` |
| 左 Shift | `LSHIFT` / `Shift` / `LeftShift` |
| ScrollLock | `SCROLLLOCK` / `Scroll` / `Scroll-Lock` |
| PageUp | `PAGEUP` / `PageUp` / `PGUP` / `Page-Up` / `PAGE_UP` |
| PrtSc | `PRTSC` / `PrintScreen` / `Print-Screen` |
| 菜单键 | `APPS` / `ContextMenu` / `Menu` |
| 小键盘回车 | `NUMENTER` / `NumpadEnter` / `ENTER` |
| 字母 A | `A` / `KeyA` |
| 数字 1 | `1` / `Digit1` |
| 小键盘 1 | `NUM1` / `Numpad1` |
| F13 | `F13` / `VK_F13` / `VK_7C` / `0x7C` |

不确定某个键叫什么？探测模式：

```bash
python src/keybridge.py --probe
```

按你想用的键，终端会打印标准名。

---

## 7. 网页控制台

启动后日志里有两行，地址也写到 `runtime/webui.url`：

```
Web 控制台已启用: http://127.0.0.1:8766/?t=abc123def456
控制台地址:      http://127.0.0.1:8766/?t=abc123def456
```

双击 `console.cmd` 会直接打开它（没在跑就先拉起来）。

### 操作对照

| 想干什么 | 怎么做 |
|---|---|
| 加一条热键 | 点「+ 新建」，用「+ 捕获」按下触发键 |
| 改按键 | 点「+ 捕获」，按新的键（Esc 取消） |
| 改动作 | 在「动作类型」下拉选，下面表单跟着变 |
| 一键生效 | 点 **「保存并生效」**（或 `Ctrl+S`） |
| 试一下 | 卡片右上角「试一下」，不按键也能执行 |
| 临时停用 | 取消左边复选框，保存 |
| 看日志 | 切到「日志」标签，3 秒自动刷新 |
| 回退 | 把 `config.json.bak` 改回 `config.json` |

### 安全设计

- 只监听 `127.0.0.1`，非本机请求直接 403
- 每次启动随机 token（`secrets.token_hex(8)`），在 URL 的 `?t=` 里
- 本机回环请求免 token（方便双击 `console.cmd`）
- 保存走「写 tmp → 备份 `.bak` → `os.replace` 原子替换」
- 端口被占自动往后试 8 个

### API

| 方法 | 路径 | 干什么 |
|---|---|---|
| GET | `/` | 控制台页面（仅本机） |
| GET | `/api/state` | 当前配置 + 生效条数 + 键名表 |
| GET | `/api/log?n=200` | 日志尾部 N 行 |
| POST | `/api/config` | 保存配置并热重载 |
| POST | `/api/test` | 执行一个动作（body 是动作对象） |
| POST | `/api/reload` | 从磁盘重读配置 |

`POST /api/test` 的 body **必须是带 `type` 的 JSON 对象**：

```bash
curl -X POST "http://127.0.0.1:8766/api/test?t=<token>" \
     -H "Content-Type: application/json" \
     --data-binary '{"type":"shell","shell":"cmd","command":"echo hi"}'
```

> ⚠️ 用 `curl -d` 会因 shell 转义丢引号，服务端回
> `{"ok": false, "error": "动作必须是带 type 的对象"}`。用 `--data-binary @file` 或 Python `urllib`。

---

## 8. HTTP 触发通道

除了按键，还能用 HTTP 触发（给流式台、手机、脚本用）。

```json
{
  "httpTrigger": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8765,
    "token": "your-secret"
  }
}
```

```bash
curl "http://127.0.0.1:8765/t/scrolllock-notepad"   # 触发指定 id
curl "http://127.0.0.1:8765/ping"                   # 探活
curl -X POST "http://127.0.0.1:8765/" -d '{"type":"run","program":"notepad.exe"}'
```

> `POST /` 是**无鉴权**的本地执行入口。只在 `127.0.0.1` 上开，别绑 `0.0.0.0`。

---

## 9. 命令行参数

```
python src/keybridge.py                    用同目录 config.json 启动
python src/keybridge.py -c my.json         指定配置
python src/keybridge.py --web              启动后自动开浏览器
python src/keybridge.py --no-web           本次不启用控制台
python src/keybridge.py --test <id>        不装钩子，只跑一次动作（自测）
python src/keybridge.py --probe            按键探测模式
python src/keybridge.py --list             列出所有热键
python src/keybridge.py --allow-injected   也响应模拟按键（默认过滤，防止自激）
```

> ⚠️ `--allow-injected` 平时**别加**。加了之后钩子会响应 keybridge 自己 `keys` 动作
> 发出的按键，可能自激死循环。只在自动化注入测试时用。

---

## 10. 开机自启

把 `start.cmd` 的快捷方式放进启动文件夹：

```
Win+R → shell:startup → 把 start.cmd 的快捷方式拖进去
```

或用任务计划程序，触发器选「登录时」；勾「不管用户是否登录都要运行」可以更早启动。

---

## 11. FAQ 与踩坑记录

### 按了键没反应

按顺序查：

1. `python src/keybridge.py --list` —— 热键加载了吗？`enabled` 是不是 `false`？
2. `python src/keybridge.py --probe` —— 按你的键，终端有没有打印键名？
   - 没打印 → 驱动没把键发出来，检查改键是否「已应用到设备」
   - 打印了但名字不对 → 键名写错了，照探测结果改
3. 看 `logs/keybridge.log` —— 有没有 `触发 [...]` 那行？
   - 有触发但动作没效果 → 问题在动作配置，不在按键
4. 如果你用模拟按键测试：默认**不响应**注入键

### `console.cmd` 报一串「不是内部或外部命令」

`console.cmd` 是 **GBK 编码**的。用编辑器改过并存成 UTF-8 后，cmd 会逐字节拆坏中文。

**正确做法**：改 `src/_mkconsole.py`，然后重新生成：

```bash
python src/_mkconsole.py
```

### `timeout: invalid time interval '/t'`

PATH 里有 GNU coreutils（Git for Windows / MSYS2）时，`timeout` 会被解析成 GNU 版本。
keybridge 的启动脚本已改用 `ping -n N 127.0.0.1 >nul` 做延时。

### 端口被占用

日志会显示：

```
端口 8765 不可用（[WinError 10013] ...），换 8766 试试
```

会自动往后试 8 个端口，真实端口写进日志和 `runtime/webui.url`。
（实测本机 8765 常被耳机驱动的 `HeadsetService` 占着。）

### 我填了桌面快捷方式的路径，按了键却没反应

看日志：

```
启动: ['D:\桌面\某应用.lnk'] (cwd=, hidden=True, console=False)
CreateProcess 打不开（WinError 193），改用 ShellExecute 打开: D:\桌面\某应用.lnk
ShellExecute 已打开: D:\桌面\某应用.lnk
```

三行都有但程序没出现 → 见下一条。

### 日志一切正常，但程序就是没出现（尤其 Electron 类程序）

先查环境变量：

```powershell
Get-ChildItem Env: | Where-Object Name -like 'ELECTRON*'
```

有 `ELECTRON_RUN_AS_NODE=1` 说明父进程（IDE / 终端 / 某些启动器）导出了它。
keybridge 会主动剥离，但若目标是通过**中间脚本**启动的，中间那层可能又加回来。

### 前台是管理员权限的游戏时，按键还能收到吗

**能。** UIPI 只拦「低完整性进程发出的模拟按键」，不拦「低级键盘钩子读取真实硬件按键」。
实测：Medium 完整性的 keybridge 常驻、前台是 High 完整性的游戏，照样收到真实按键。

但**自动化注入**（`keybd_event` / `SendInput`）在那种情况下会被静默丢弃 —— 那时需要提权。

### 为什么用 F13 做触发键不行

F13–F24 是「扩展功能键」，标准键盘没有，要靠驱动或软件**主动发出来**。
狼蛛 Aula Hub 的键位表里根本没有 F13（见第 3 节），所以选不到。

想用 F13：AutoHotkey、部分宏键盘、QMK/VIA 固件的键盘可以发。

### 会不会被杀软报毒

低级键盘钩子 + 拉起任意进程 —— 这个组合在启发式引擎眼里确实可疑。
keybridge 本身不联网、不写注册表、不隐藏自己，源码全部可读。被拦就把目录加白。

---

## 12. 逆向溯源

Aula Hub（狼蛛外设驱动 v2.5.3）的 Electron 应用完整反混淆结论。

### 为什么确定它做不到「按键 → 跑程序」

| 检查项 | 结果 |
|---|---|
| `main-deob.js` 里 `globalShortcut` 引用 | **0 次** |
| `shell.openExternal` / `shell.openPath` 调用 | **0 次**（只有 `import { shell }`，从未调用） |
| IPC 通道全表 | 无任何「跑程序 / 跑命令」入口 |
| 动作类型 | 改键、组合键 CB、封装键 EK、**网址键 WEB（只能开 URL）**、快捷指令宏、高级键 DKS/SOCD/MT/TGL |
| 宏固件格式 | `A = ((status===1?1:0)<<31) \| (delay<<16) \| (keyCode & 65535)` —— 键码空间只有 16 位 HID，塞不下命令行 |
| 唯一带「运行程序」语义的 | GSI（CS2 死亡/闪光事件），绑的是**游戏事件**，与按键无关 |

### 键位表文件

| 文件 | 用途 |
|---|---|
| `asar/dist/assets/DEdNpXFo.js`（197010 B） | 改键页 UI 用的表，`'ScrollLock':{'value':0x47,'text':...,'type':0x1}` |
| `asar/dist/assets/BS844IfY.js`（148953 B） | 更紧凑的一份，`{'value':0xNN,'type':0xN}` |
| `asar/dist/assets/svncPnbJ.js`（3518662 B） | 未混淆的 SDK/协议核心（宏格式、HID 连接参数、私有功能码表） |

`src/_aula_keys.py` 就是解析前两张表的脚本。完整反混淆工具链（asar 解包、
obfuscator.io 反混淆、内嵌 PowerShell 还原）不在本仓库里。