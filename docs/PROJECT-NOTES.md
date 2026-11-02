# 设计说明与风险清单

> 这是工程笔记，不是用户文档。想上手看 [README](../README.md) 或 [USAGE](USAGE.md)。

---

## 一、交付物

| 项 | 内容 |
|---|---|
| 主程序 | `src/keybridge.py`（约 1200 行，纯标准库 + ctypes） |
| 控制台 | `src/webui.py`（内置页面 + REST API） |
| 打包脚本 | `build.py`（PyInstaller 单文件 exe + zip） |
| 键位表工具 | `src/_aula_keys.py` + `data/aula-keys.json` |
| 端口/编码工具 | `src/_mkconsole.py` |
| 测试 | `tests/`（逻辑 36 项 / 回调 24 项 / API 35 项 / 热重载 14 项 / 页面检查） |
| 文档 | `README.md`、`docs/USAGE.md`、`docs/aula-keys.txt` |

---

## 二、验证记录

全部为实机跑过的结果，不是"应该能用"。

| 项 | 方法 | 结果 |
|---|---|---|
| 动作通道 | 九种动作各跑一次，写日志断言 | **10/10 通过** |
| 热键逻辑 | `tests/_logictest.py`（替身 runner 直接喂按键，绕开 Windows 钩子） | **36 项通过** |
| 钩子回调 | `tests/_callbacktest.py`（喂真实 `KBDLLHOOKSTRUCT`） | **24 项通过** |
| 控制台 API | `tests/_webuitest_run.py` | **35 项通过** |
| 热重载链路 | `tests/_webui_hot.py`（新增→生效→改动作→新动作生效且旧动作不再触发→停用→删除） | **14 项通过** |
| 页面 JS | `tests/_pagecheck.mjs`（`new Function(js)` 语法检查 + 关键函数存在性） | 全绿 |
| 端到端（注入） | 提权子进程自建窗口 + 注入 F13–F18 | **7/7 通过** |
| 罕见键注入 | ScrollLock / Pause / Insert / Home / End / PageUp / PageDown / PrtSc / NumLock / Apps | **10/10 命中** |
| 真实配置端到端 | ScrollLock→启动应用、Pause→cmd、Insert→内联 Python、Home→脚本 | **4/4 通过** |
| HTTP 触发 | `/ping`、`/t/<id>`、`POST /` | **3/3 通过** |
| `.lnk` 作为 program | 三种写法（run+lnk / run+exe+cwd / url+lnk） | 全部拉起成功 |
| Electron 环境剥离 | 父进程有 `ELECTRON_RUN_AS_NODE=1`，子进程为 `None` | 通过 |
| 打包版 | exe 的 `--list` / `run` / `shell` / `code` / `script` / webui | 全部通过 |
| 打包版中文路径 | 放在 `D:\测试中文目录\` 下运行 | 通过 |

---

## 三、做对了什么

### 1. 先定死了"能不能"再动手

逆向 Aula Hub 之后先用证据确认它**没有**「按键 → 跑程序」的能力
（`globalShortcut` 0 次、`shell.openExternal` 0 次、IPC 全表无入口、宏格式只有 16 位键码），
才决定走"驱动管键、代理管动作"的分工。如果先写代码、后来才发现驱动其实能做，就白干。

### 2. 没有一上来就要求管理员权限

低级键盘钩子 `WH_KEYBOARD_LL` 的直觉印象是"要提权"，但实测：

- 以 Medium 完整性常驻，前台是 High 完整性的游戏时，
  **钩子照样能读到真实硬件按键**（抓到了用户正在输入的 `T P S LSHIFT …` 等）
- UIPI 只拦"低完整性进程**发出的模拟按键**"，不拦"钩子**读取真实按键**"

所以日常使用不需要管理员权限。这个结论省掉了安装门槛，也避免了 UAC 弹窗。

### 3. 日志队列化

钩子回调跑在系统消息处理路径上。在里面直接做文件 I/O，一旦慢下来会被系统
**静默摘掉钩子** —— 表现是"跑几次之后完全没反应，无任何报错"。
用 `QueueHandler` + `QueueListener` 把 I/O 挪到独立线程后彻底稳定。

### 4. 把"驱动能选哪些键"从推理变成数据

一开始想当然用了 F13（因为 Aula 的协议层认识 `funcF13:2100`）。
但用户反馈"驱动里改不了 F13"。于是去 dump 改键页自己的两张键位表
（`DEdNpXFo.js` 与 `BS844IfY.js`），各 114 条，`F1` 到 `F12` 就结束了，`grep F13` 零命中。

**协议层认识某个码 ≠ 用户在 UI 里能选到它。**

### 5. 打包成 exe 之后又跑了一遍全部动作

打包版有它自己的坑：`sys.executable` 指向 exe 自己，`script` 动作里
`[sys.executable, "-u", f]` 会变成"用 exe 去跑 .py"，报 `NotADirectoryError`。
这个 bug 只在打包版出现，源码运行完全正常 —— 不实际跑一遍就发现不了。

---

## 四、绕了远路的地方

### 1. 抢前台花了太多时间（5 种手法全失败）

为了让"注入的按键"能被系统接受（UIPI 问题），试过：
`SetForegroundWindow`（返回 0）、`SwitchToThisWindow`（返回 True 但无效）、
`SetActiveWindow`（返回 True 但前台不变）、ALT 技巧、`AttachThreadInput`+TOPMOST（err=5）、
改 `ForegroundLockTimeout` 注册表 + SPI（SPI 读回始终 `2147483647`）、私有桌面。

**教训**：遇到"注入没反应"，第一件事应该是查**前台窗口的完整性级别**，
而不是想办法抢前台。`GetForegroundWindow` → 取 PID → `OpenProcessToken` → 取 IL，
三行代码就能定性。前面 5 种手法加起来浪费了好几个小时。

### 2. `.lnk` 启动不了，挖了两圈才到底

现象：进程起来了、2 秒后退出、**ExitCode=0、无窗口、无输出**。

第一圈以为是快捷方式解析问题（`.lnk` → 目标 exe + 工作目录），
第二圈以为是目标程序自己的启动参数问题。

真因：`ELECTRON_RUN_AS_NODE=1` 被 keybridge 从宿主继承，又传给子进程，
Electron 应用看到它就退化成纯 Node 解释器。

**教训**：遇到"进程起来了又安静退出、退出码 0、没有输出"，
**先查环境变量继承，再查目标程序自己**。`app_env_audit` 这样的工具本就在手边。

### 3. F13 这个键选得太想当然了

见"做对了什么"第 4 条。如果一开始就去 dump 键位表，就不用改配置、改文档、改示例。

### 4. 控制台页面的 HTML 拼接方式返工

第一版用 Python 普通字符串 + JS 单引号拼 HTML 属性，转义全乱（引号、反斜杠、中文）。
改成"Python 原始字符串 `r"""` + JS 模板字符串（反引号）"之后一次通过。

**教训**：生成 HTML 时，外层用原始字符串、内层用模板字符串，两层都不会互相转义。

### 5. 批处理编码反复踩

`console.cmd` 一开始存 UTF-8，cmd.exe 按 GBK 解码 → 中文被逐字节拆坏 →
报 `'gs" mkdir "logs"' 不是内部或外部命令`。
`for /f` 反引号里的管道写成 `^|` 后 PowerShell 收到字面 `^`。
`timeout /t` 被 PATH 里 Git 的 GNU `timeout` 抢走。

**教训**：Windows 批处理里有三件事必须当成"环境依赖"处理 ——
编码（GBK）、外部命令同名冲突（`timeout`）、跨 shell 转义（`^|`）。
一律用生成脚本（`_mkconsole.py`）产出，不要手写。

---

## 五、留下的债

1. **只支持 Windows**。`SetWindowsHookExW` / `SendInput` / ShellExecuteW 都是 Win32 API。
   跨平台要走 `pynput` + 各平台原生 API，工作量不小。

2. **`ELECTRON_STRIP_ENV` 是白名单式的**，靠人往里加变量。
   将来 Electron 新增注入类变量就得手工补。更好的做法是改成"只保留必要变量"的黑名单模式，
   但那会破坏很多程序的正常启动（PATH、APPDATA 等都要留）。

3. **`--allow-injected` 与 `keys` 动作的组合有自激风险**。
   目前靠"默认过滤注入键"回避，没有真正的环路检测。

4. **HTTP 的 `POST /` 无鉴权**。只在 `127.0.0.1` 上开，风险可控，
   但如果用户手改成 `0.0.0.0` 就是本机任意代码执行入口。目前只在文档里警告。

5. **键位表只有 Aula Hub 一份**。其他品牌驱动（罗技、雷蛇、达尔优…）需要各自 dump。

6. **没有单元测试框架**。测试脚本是"打印通过/失败并计数"的自研格式，
   不是 pytest。好处是零依赖，坏处是不能集成到 CI 的标准报告里。

7. **打包版的 `script` 动作依赖系统里有 Python**。
   `code` 动作里的 `python` 类型同理。如果用户机器上没装 Python，
   只有 `powershell` / `bat` / `js`（需要 node）能用。
   要彻底解决得把 Python 运行时也打进 exe（体积会涨到 30 MB+）。

8. **多实例没有统一协调**。地址文件按配置名分离了，但两个实例监听同一颗键时
   会同时触发两次。目前靠"别开两个"约定。

---

## 六、后续风险清单

按「后果 × 概率」排序。

| # | 风险点 | 触发条件 | 后果 | 前兆 | 对策 |
|---|---|---|---|---|---|
| 1 | `POST /` 无鉴权本地执行 | 用户把 `httpTrigger.host` 改成 `0.0.0.0`，且防火墙放行 | 同网段任意机器可在本机执行任意命令 | 日志出现非 `127.0.0.1` 来源的 POST；`netstat` 显示 8765 监听在 `0.0.0.0` | 只绑 `127.0.0.1`；如需远程用 SSH 隧道；给 `POST /` 加 token 校验 |
| 2 | `swallow: true` 配到常用键 | 误把 `Ctrl` / `Space` / `Enter` 之类设成 `swallow: true` | 那几个键彻底失灵，打字/操作被打断，且不易联想起是 keybridge 干的 | 某个键突然"没反应"；`--list` 里能看到该键带 `swallow` | 只对 `ScrollLock`/`Pause`/`Insert` 这类罕见键开 swallow；出事先 `stop.cmd` |
| 3 | 钩子被系统静默摘掉 | 在钩子回调里做同步 I/O、弹窗、写日志（不经队列） | 热键全部失灵，日志里**没有任何报错**，看起来像"程序还在跑但不干活" | 日志停在某个时刻不再增长，但进程还在；重启后恢复 | 保持 `QueueHandler` 队列化；任何新加的回调逻辑不许做 I/O |
| 4 | 保存配置写坏 | 磁盘满 / 断电 / 两个控制台同时保存 | 配置文件变空或半截，启动时全部热键丢失 | 启动日志"已加载 0 条热键"；`config.json` 体积异常小 | 已有 `.bak` 备份 + `os.replace` 原子替换；出问题用 `.bak` 覆盖回去 |
| 5 | `ELECTRON_RUN_AS_NODE` 漏网 | 新版本 Electron 引入新的注入变量，或目标通过中间脚本启动 | 目标程序静默退出，退出码 0，用户以为"按键没生效" | 日志三行齐全（启动/转 ShellExecute/已打开）但无进程；`Get-ChildItem Env:ELECTRON*` 有值 | 往 `ELECTRON_STRIP_ENV` 补变量；用 `app_env_audit` 类工具自检 |
| 6 | 驱动侧没真正应用改键 | 改完没点"应用/下载到设备"，或键盘被 Win 锁 / 游戏模式锁住 | 按键探测模式收不到任何键 | `--probe` 按目标键终端无输出 | 先用 `--probe` 验证驱动确实发了这个键，再配 keybridge |
| 7 | F13 类键误判为"工具坏了" | 用户按网上教程想用 F13–F24 | 配好了但永远不触发，怀疑 keybridge 有 bug | `--probe` 完全收不到 F13 | 文档已明确说明；`_aula_keys.py` 可自查；改用 `ScrollLock` |
| 8 | 端口被占 | 耳机驱动 / 其他服务占了 8765 | 控制台打不开（HTTP 触发也会换端口） | 日志"端口 8765 不可用，换 8766 试试" | 已实现自动回退 8 个端口；`runtime/webui.url` 总是真实地址 |
| 9 | token 通过截图/日志外泄 | 用户把控制台地址截图发出去 | 同机其他用户（多用户场景）可改配置 | 无明显前兆 | 每次启动重新生成 token；关掉浏览器标签即失效 |
| 10 | 子进程继承宿主的敏感环境变量 | keybridge 从 IDE/终端启动，那个环境里有 `AWS_SECRET_ACCESS_KEY` 等 | 被拉起的程序能看到不该看的凭据 | 无 | 用 `env` 字段显式覆盖；或从干净环境（`start.cmd`）启动 keybridge |
| 11 | 打包版缺 Python | 用户机器没装 Python，却配了 `script`/`code(python)` 动作 | 动作报 `FileNotFoundError` | 日志"启动: ['python', '-u', ...]" 然后 WinError 2 | 文档已注明；改用 `powershell`/`bat`；或设 `KEYBRIDGE_PYTHON` |
| 12 | 多实例重复触发 | 开两个 keybridge 实例监听同一颗键 | 一次按键跑两遍动作（比如开两个程序） | 同一动作的日志出现两条时间戳极近的记录 | 单实例互斥体已按配置名隔离；不同配置名仍需人工避免 |
| 13 | 杀软启发式拦截 | 低级键盘钩子 + 拉起任意进程的行为模式 | 被隔离/删除，或按键被拦 | 程序莫名消失；杀软日志有拦截记录 | 源码可读、不联网、不写注册表；目录加白 |
| 14 | 长按计时器被无关键干扰 | （已修复）`_on_up` 无条件 cancel | `trigger: long` 永不触发 | 日志只有 down 没有"触发" | 已改为"只有本热键用到的键抬起才 cancel"；`_logictest.py` 覆盖此路径 |
| 15 | 配置文件热重载读到半截文件 | 编辑器保存瞬间正好触发 2 秒轮询 | 那一次重载失败，旧配置继续生效（不会崩） | 日志"热重载失败" | 已有 digest 比对 + 失败保留旧配置 |
| 16 | `runtime/tmp` 无限增长 | 大量不同的 `code` 动作（每次内容不同 → 新文件） | 磁盘占用缓慢增长 | `runtime/tmp` 文件数持续变多 | 手动清理；或定期删该目录（不影响运行） |

---

## 七、关键实现细节

### 7.1 ctypes 必须声明 restype

64 位下不声明 `restype`，句柄会被按 32 位 int 截断：

```python
kernel32.GetModuleHandleW.restype = wt.HMODULE
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [c_int, c_void_p, wt.HMODULE, wt.DWORD]
user32.CallNextHookEx.restype = c_ssize_t
kernel32.CreateMutexW.restype = wt.HANDLE
shell32.ShellExecuteW.restype = ctypes.c_void_p
```

否则 `SetWindowsHookExW` 返回 0，`GetLastError()` 报
`126 (ERROR_MOD_NOT_FOUND)` —— 看起来像"找不到模块"，实际是句柄被截断。

### 7.2 钩子回调里绝不能做 I/O

```python
setup_log(cfg, base_dir)   # 内部用 QueueHandler + QueueListener
log.propagate = False
```

回调跑在系统消息处理路径上，任何同步 I/O 慢一点，系统会**静默摘钩子**。

### 7.3 `DETACHED_PROCESS` 会让 PowerShell 静默不执行

实测对照：

| 组合 | 结果 |
|---|---|
| `DETACHED_PROCESS` | ❌ 进程在、脚本没跑、无报错 |
| `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` | ❌ 同上 |
| `CREATE_NO_WINDOW` | ✅ |
| `DETACHED_PROCESS \| CREATE_NO_WINDOW` | ❌ |
| `CREATE_NEW_CONSOLE` | ✅ |

结论：用 `CREATE_NO_WINDOW`，不要用 `DETACHED_PROCESS`。
`CREATE_BREAKAWAY_FROM_JOB` 在普通用户下直接 `WinError 5 拒绝访问`。

### 7.4 改环境变量是进程级操作，必须加锁

`ShellExecuteW` 没有 `env` 参数，新进程直接继承调用者环境。
为了让 Electron 应用能正常启动，只能在调用期间临时 pop 环境变量：

```python
with _SHELL_ENV_LOCK:
    saved = {}
    for k in ELECTRON_STRIP_ENV:
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    try:
        rc = shell32.ShellExecuteW(...)
    finally:
        os.environ.update(saved)
```

不加锁的话，两个并发动作会互相看到对方改了一半的环境。

### 7.5 键名解析顺序

```python
def vk_of(name):
    n = canon(name)                     # 大写、去空格连字符、查别名表
    if n in NAME_TO_VK: return NAME_TO_VK[n]
    if n.startswith("VK_"):
        rest = n[3:]
        if rest in NAME_TO_VK:          # VK_F13 → F13 → 0x7C
            return NAME_TO_VK[rest]
        return int(rest, 16)            # VK_7C → 0x7C
    if n.startswith("0X"):              # 0x7C
        return int(n, 16)
    if n.startswith("KEY") and len(n) == 4 and n[3].isalpha():
        return 0x41 + (ord(n[3]) - 0x41)      # KeyA → 0x41
    if n.startswith("DIGIT") and len(n) == 6:
        return 0x30 + int(n[5])               # Digit1 → 0x31
    if n.startswith("NUMPAD") and len(n) == 7:
        return 0x60 + int(n[6])               # Numpad1 → 0x61
    return None
```

**坑**：早期把 `VK_F13` 直接当十六进制解析，得到 `3859`。
必须先查表、再退化成十六进制。

### 7.6 钩子去重的状态机

```
_on_down(vk):
    if vk in down:                      # 系统自动重复
        swallow ? 吞 : (repeat 的热键 → 派发 "repeat")
        return
    down.add(vk); 合并 tags
    for hk in hotkeys:                  # 复位 _fired，让 up 触发可以再来一次
        if vk in hk._vks and not hk.repeat:
            hk._fired = False
    匹配 → swallow 时记 swallow_vk
         → trigger=="long" 启 Timer
         → trigger=="down" 立即 _fire

_on_up(vk):
    if vk not in down: return
    down.remove(vk); 重算 tags
    for hk in hotkeys:
        if hk._timer is not None and vk in hk._vks:   # ← 只取消自己的
            hk._timer.cancel(); hk._timer = None
        if hk.trigger == "up" and not hk._fired and hk.matches(...):
            _fire(...)
```

**两个修过的真 bug**：

1. `_on_up` 原本无条件 `hk._timer.cancel()`，
   于是**任意无关键**（用户敲的 E/Q/W）抬起都会掐掉长按计时器 → 长按永不触发。
2. `_fired` 原本在 `_on_up` 里复位，等于没去重 → 遇到重复 UP 事件会连续触发。
   挪到 `_on_down` 才正确。

---

## 八、仓库结构

```
keybridge/
├── src/
│   ├── keybridge.py       主程序（钩子 + 热键引擎 + 动作派发 + HTTP）
│   ├── webui.py           网页控制台（内置页面 + REST API）
│   ├── _mkconsole.py      生成 GBK 编码的 console.cmd
│   └── _aula_keys.py      dump 驱动可选键表
├── data/
│   └── aula-keys.json     Aula Hub 键位表（114 条，已 dump）
├── examples/
│   ├── config.minimal.json
│   └── config.advanced.json
├── scripts/
│   ├── demo.py
│   └── demo.ps1
├── tests/
├── docs/
├── build.py
└── README.md
```