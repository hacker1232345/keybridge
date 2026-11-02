# keybridge

> 按下一个键 → 运行你指定的**程序 / 命令 / 代码**。
>
> 纯 Python 标准库 + ctypes，**零第三方依赖**，**不需要管理员权限**，Windows 10/11。

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey)]()
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Deps](https://img.shields.io/badge/Dependencies-none-brightgreen)]()

---

## 这是干什么的

很多键盘驱动（狼蛛 Aula Hub、罗技 G HUB、雷蛇 Synapse …）的「自定义按键」只能做到
**把 A 键映射成 B 键**，或者打开一个网址。想让它**跑一个 exe、执行一条命令、运行一段代码**——做不到。

keybridge 补上这一段：

```
  物理键盘
      │  ① 你在键盘驱动里把某颗键改成 ScrollLock / Pause / Insert 这类没人用的键
      ▼
  驱动固件发出那个键
      │  ② keybridge 用低级键盘钩子（WH_KEYBOARD_LL）监听它
      ▼
  keybridge 执行你配的任意动作
      run / shell / script / code / url / keys / sequence / http / opendir
```

**驱动负责「哪个键」，keybridge 负责「按下之后干什么」。**

九种动作、组合键、长按触发、按键吞掉、按住连发、网页控制台一键改配置、HTTP 触发通道。

---

## 30 秒上手

### 方式一：下载成品（不用装 Python）

1. 到 [Releases](https://github.com/hacker1232345/keybridge/releases) 下载最新的 `keybridge-*-win64.zip`
2. 解压到任意目录（例如 `D:\Tools\keybridge`）
3. 双击 **`console.cmd`** —— 浏览器会自动打开控制台
4. 在控制台里点「+ 新建」，用「+ 捕获」按下你的触发键，填好动作，点「保存并生效」

### 方式二：源码运行

```bash
git clone https://github.com/hacker1232345/keybridge.git
cd keybridge
python src/keybridge.py -c examples/config.minimal.json --list
```

需要 Python 3.8+（**只用标准库**，不用 pip install 任何东西）。

---

## 一个最小可用的配置

```json
{
  "logFile": "logs/keybridge.log",
  "logLevel": "INFO",
  "hotkeys": [
    {
      "id": "scrolllock-notepad",
      "name": "ScrollLock → 打开记事本",
      "keys": ["SCROLLLOCK"],
      "trigger": "down",
      "swallow": true,
      "action": { "type": "run", "program": "notepad.exe" }
    }
  ]
}
```

```bash
python src/keybridge.py -c my.json
```

按下 `ScrollLock`，记事本就起来了。

---

## 九种动作

| 类型 | 干什么 | 关键字段 |
|---|---|---|
| `run` | 启动程序 | `program`（exe / `.lnk` / `.bat` / 裸程序名）、`args`、`cwd`、`hidden`、`console` |
| `shell` | 执行命令 | `command`、`shell`（`cmd` / `powershell` / `pwsh` / `bash`） |
| `script` | 运行脚本文件 | `file`、`interpreter`（按扩展名自动推断） |
| `code` | 运行内联代码 | `lang`、`code`（落成临时文件再跑） |
| `url` | 打开网址 / 文件 / 目录 | `url` |
| `keys` | 模拟按键 | `keys` 或 `sequence`、`delay` |
| `sequence` | 串行做多件事 | `steps[]`、每步可带 `waitAfter` |
| `http` | 发 HTTP 请求 | `url`、`method`、`headers`、`body` |
| `opendir` | 打开文件夹 | `dir` |

完整字段表见 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 为什么不需要管理员权限

这是最容易误解的一点，实测结论：

**Windows 的 UIPI（User Interface Privilege Isolation）只拦「低完整性进程发出的模拟按键」，
完全不拦「低级键盘钩子读取真实硬件按键」。**

所以：

| 场景 | 需要管理员？ |
|---|---|
| 监听你的真实按键（日常使用） | ❌ 不需要 |
| 前台是管理员权限的游戏/程序时，依然能收到你的按键 | ❌ 不需要 |
| 用 `keybd_event` / `SendInput` 做自动化注入测试 | ✅ 需要（否则注入被静默丢弃） |

实测证据：以 Medium 完整性常驻、前台是 High 完整性的游戏时，钩子照样抓到了正在输入的
`T P S LSHIFT SEMICOLON …` 等真实按键。

---

## 网页控制台

```
┌─ keybridge 控制台 ────────────────────────── ● 运行中 · 已生效 3 条
│  [热键]  [日志]                            D:\Tools\keybridge\config.json
│
│  ┌────────────────────────────────────────────────────┐
│  │ ☑ ScrollLock → 打开记事本           [试一下] [删除]  │
│  │   触发按键  [SCROLLLOCK] [+ 捕获]                    │
│  │   触发方式  [按下触发▾]   [x] 吞掉这个键  [ ] 按住连发 │
│  │   动作类型  [run · 启动程序▾]   程序 [notepad.exe]    │
│  └────────────────────────────────────────────────────┘
│                          [放弃修改]  [保存并生效] ← 点这个
└────────────────────────────────────────────────────────
```

- 只监听 `127.0.0.1`，非本机请求 403
- 每次启动随机生成 token，写在 `runtime/webui.url`
- 保存走「写 tmp → 备份 `.bak` → 原子替换」，改坏了能回退
- 保存即热重载，**不用重启**（钩子不会掉）
- 端口被占用会自动往后试 8 个

---

## 项目结构

```
keybridge/
├── src/
│   ├── keybridge.py       主程序（钩子 + 热键引擎 + 动作派发 + HTTP）
│   ├── webui.py           网页控制台（内置页面 + REST API）
│   ├── _mkconsole.py      生成 GBK 编码的 console.cmd
│   └── _aula_keys.py      dump 狼蛛 Aula Hub 的可选键表
├── examples/
│   ├── config.minimal.json    最小可用配置
│   └── config.advanced.json       8 条可直接抄的实例
├── scripts/
│   ├── demo.py            示例脚本
│   └── demo.ps1
├── tests/
│   ├── _logictest.py      逻辑单测（36 项，不需要真实按键）
│   ├── _callbacktest.py   钩子回调单测（24 项，真实结构体）
│   ├── _webuitest_run.py  控制台 API（35 项）
│   ├── _webui_hot.py      热重载链路（14 项）
│   └── _pagecheck.mjs     页面 JS 语法检查
├── docs/
│   ├── USAGE.md           完整使用手册
│   ├── PROJECT-NOTES.md   复盘 + 风险清单
│   └── aula-keys.txt      Aula Hub 键位表 dump（114 条）
├── build.py               打 Release 用的 zip
└── README.md
```

---

## 测试

```bash
python tests/_logictest.py         # 36 项，不需要装钩子，纯逻辑
python tests/_callbacktest.py      # 24 项，直接喂真实 KBDLLHOOKSTRUCT
python tests/_webuitest_run.py     # 35 项，控制台 API（要先起一个带 webui 的实例）
node   tests/_pagecheck.mjs        # 页面 JS 语法 + 关键函数检查
```

全部为纯标准库 / 零依赖，跑完打印「通过 N 项，失败 0 项」。

---

## 已知边界

| 项 | 状态 |
|---|---|
| 平台 | 仅 Windows（用了 `SetWindowsHookExW` / `SendInput` / ShellExecuteW） |
| 键位选择 | 受驱动限制。例如狼蛛 Aula Hub 的键位表只有 114 条，**不含 F13–F24**，只能选 `ScrollLock`/`Pause`/`Insert` 这类 |
| 管理员权限 | 日常不需要；自动化注入测试需要 |
| 多显示器 / 高 DPI | 无关（不做 UI 抓取） |
| 杀软误报 | 低级键盘钩子 + 拉起进程的行为模式可能被启发式引擎盯上，可自行加白 |

---

## 许可

[MIT](LICENSE)

本项目只做**本机按键 → 本机动作**的桥接，不涉及任何网络通信（除了你自己配的 `http` 动作）。
用它跑什么、在谁的机器上跑，由使用者自己负责。