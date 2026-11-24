# 交接文档（新会话先读这一份）

> 最后更新：**2026-11-18 15:30（CST）**
>
> 本文件面向接手的 AI 会话 / 新维护者，只讲四件事：**这是什么、现在什么状态、
> 运行链怎么走、哪些坑不能踩**。完整背景与历史故障记录在
> [`LOCAL_SETUP.md`](LOCAL_SETUP.md)，本文件不重复。
>
> 文档已脱敏：不包含任何真实好友 / 群昵称、Cookie、Webhook。用 `好友NN`
> 指代 `config.json` 中 `targets` 的第 NN 项，与 `artifacts/result.json` 里的别名一致。

---

## 1. 项目一句话

上游 [`<upstream-owner>/douyin-auto-fire`](https://github.com/<upstream-owner>/douyin-auto-fire) 的 fork
（基线 commit `af0035f`，2026-09-11）。用 Python + Playwright 登录抖音网页版，
**每天按 `config.json` 给一批好友 / 群发消息，保持火花不断**。

- **上游代码（`app/`、`run.py`、`config/`）一行未改。**
- 本机新增全部在 `scripts/`、`deploy/`、`.gitignore`、`LOCAL_SETUP.md`、本文件。
- 当前配置：**15 个目标 × 每条目 2 条消息 = 每天应有 30 条**。
  2 条 = 文字「叮！自动续火花」+ 一个随机抖音原生表情（比心 / 开心）。
- 时区 `Asia/Shanghai`，按自然日结算。

## 2. 接手前必须知道的 6 件事

1. **项目真实路径是 `<PROJECT_ROOT>`。**
   `~/Desktop/douyin` 只是符号链接。**绝对不要移回桌面**：macOS TCC 禁止
   launchd 进程访问 `~/Desktop`，任务会以退出码 126 直接失败。原因与实测见
   `LOCAL_SETUP.md` 开头。
2. **定时由 launchd 管，不是 cron。** 已安装
   `~/Library/LaunchAgents/com.example.douyin-fire.plist`，每天
   **00:10 / 09:30 / 15:00 / 20:00** 各触发一次 + 每次登录触发一次（`RunAtLoad`）。
3. **四个时间点不是每天发四次。** `scripts/daily_run.sh` 会在开浏览器**之前**
   检查账本，今天已全部成功就直接退出。
4. **权威数据是 `artifacts/history.json`，不是 `result.json`。**
   `history.json` 是防重复账本，只在真实发送时写入；`result.json` 每次运行（包括
   `--dry-run`）都会被覆盖。判断"今天做完没有"只能看 `history.json`。
5. **退出码语义**：`0` 全部成功；`1` 部分目标失败；`2` 配置 / 登录 / 风控 /
   已有任务在跑；`130` 被中断。通知与重试逻辑全按这个码分支。
6. **主任务报成功 ≠ 真的发到对方界面。** 成功后再由
   `scripts/verify_sent.py --fix` 做接收侧复检（看聊天里最新标记消息的时间分隔符），
   漏掉的当场补发。

## 3. 当前状态快照（2026-11-18 16:25）

`./scripts/status.sh` 的输出要点：

| 项目 | 状态 |
| --- | --- |
| launchd | 已安装，`state = not running`，累计 `runs = 20`，最近一次是手动补跑 |
| 登录凭证 | `storage-state.json` 最后更新 **2026-10-07**，仍然有效 |
| 最近 7 天 | 11/12–11/14 完整 30 条；**11/15 为 0 条**（断网）；**11/16–11/17 只有 18 条**；**11/18 已恢复 30 条** |
| 最近一次运行 | 11/18 16:16–16:25，主任务 15/15 + 接收侧复检补发 3 条，最终 15/15 确认 |
| 单元测试 | `.venv/bin/python -m pytest -q` → **223 passed**（用 `--basetemp` 避开沙箱残留目录） |

### 2026-11-18 已解决：6 个目标改备注导致搜索不到

症状与上面旧版记录一致：`好友01/02/07/09/12/15` 从 11/16 起连续报
`搜索不到目标好友`（`permanent`，不会自动重试）。根因是对方改了备注，
`config.json` 里的名字对不上号。

**已做的修复（代码 + 配置）：**

- 给 `Target` 增加 `aliases`（备用名）字段，`config.json` 里 6 个目标各配了 1 个新名字。
- `name` **保持不变**：它是 `history.json` 的账本身份。改备注时只动 `aliases`，
  不会导致重发或漏记；日志里会写 `目标 X 通过备用名 Y 打开（账本身份不变）`。
- 打开顺序：`name` → `aliases` 逐个尝试；只有「搜索不到 / 打不开」才继续试下一个，
  登录失效 / 风控立即上抛，不会用错身份继续发。
- `scripts/verify_sent.py --fix` 也走同一套别名逻辑，补发不会打不开聊天。
- 11/18 16:16–16:25 已真跑：主任务 15/15 成功、补发 12 条；接收侧复检又补发 3 条
  （账本标记成功但聊天里没找到标记消息的那 3 个），最终 **15/15 确认、30/30 条**。

**以后再有人改备注（标准三步）：**

```bash
cd <PROJECT_ROOT>
# 1. 只读反查新名字（不点会话、不发消息）
.venv/bin/python scripts/find_target.py 旧名字
# 2. 把查到的新名字填进 config.json 对应目标的 aliases 数组（不要改 name）
# 3. 验证 + 补发
./scripts/daily_run.sh --force --dry-run --no-notify
./scripts/daily_run.sh --force
```

### 2026-11-18 二次优化：昵称优先 + 已续火花自动跳过

**昵称优先**：`config.json` 每个私聊目标都有 `nickname`（从个人主页 `h1` 采集，
11/11 已填）。打开顺序是 `nickname → name（备注）→ aliases`，昵称命中就不再试别名；
账本 key 仍然是 `name`，所以改昵称不会重发或漏记。群没有昵称，沿用原名搜索。

**当日已续则跳过**：私聊里如果检测到**今天双方都发过消息**（火花已续上），
就跳过自动发送，并在 `history.json` 里以 `reason: already_renewed_today` 记为
success，避免当日后续时间点重复运行。开关 `skip_if_renewed_today`（默认 `true`）。
判定只读最近消息的时间分隔符，未知日期一律保守当成"没续"。

重新采集昵称（只读，结果写 `nicknames.json`，已 gitignore）：
```bash
.venv/bin/python scripts/collect_nicknames.py
# 一次跑 11 个容易被系统杀掉，分批更稳：
.venv/bin/python scripts/collect_nicknames.py --only 好友A,好友B
```

如果 `find_target.py` 完全查不到，再导出会话列表人工比对：
`.venv/bin/python scripts/list_chats.py --out chats.md`（导出的文件含真实昵称，已 gitignore）。
确实注销 / 解散的，才从 `config.json` `targets` 删掉，并同步删 `_targets_legend`。
注意：`make_config.py` 默认会选中**所有带火花标识的会话**，可能顺带加入你不想发的目标；
要沿用当前名单，请用 `--names-file` 明确列出 15 个名字。

### 2026-11-19 三次优化：标记文字发出即成功（表情包失败不补发）

一个目标默认发 2 条：文字「叮！自动续火花」+ 随机表情包。规则改为：

- **标记文字（text）真正发出 = 火花已续上**，表情包只是附加的点缀。
- 表情包发送失败时，若标记已发出 → 记为 success（`reason: sticker_failed_mark_sent`），
  **不判目标失败、不当天反复重跑、复检也不补发**；只有标记自身失败才算真失败。
- 登录失效 / 风控依旧立即上抛，绝不被当成"表情包失败"吞掉。
- 复检侧加了账本兜底：页面读不到标记但账本确认标记文字今天已发时，判成功不补发，
  避免重复发送已发出的文字打扰对方。

判定集中在 `app/marking.py`（`is_marker_message` / `tolerate_attachment_failure`），
主任务 `app/main.py` 和复检 `scripts/verify_sent.py` 共用；`app/history.py` 的
`mark_success` 支持记录 `reason`。测试 `tests/test_marking.py`。

## 4. 每日运行链

```
launchd (00:10/09:30/15:00/20:00 + 登录时)
  └─ scripts/daily_run.sh
       ├─ 检查 run.lock：上一次还活着 → 静默跳过
       ├─ 检查 history.json：今天已全部 success → 直接退出（不开浏览器）
       ├─ caffeinate -i .venv/bin/python run.py
       │    └─ app/main.py: run()
       │         ├─ app/browser.py   启动 Chromium / 加载 storage-state / 校验登录
       │         ├─ app/douyin.py    搜索并打开目标会话
       │         ├─ app/sender.py    发文字 / 图片 / 原生表情，并确认"终态"
       │         └─ app/history.py   写防重复账本 + 运行锁
       ├─ 退出码 2 且今天已有成功记录 → 60 秒后重试一次（瞬时抖动防护）
       ├─ 按退出码发 macOS 通知（最后一个时间点才响铃）
       └─ 退出码 0 且非 dry-run → scripts/verify_sent.py --fix
            └─ 接收侧复检 → 补发 → 再复检
```

关键设计取舍（细节见 `LOCAL_SETUP.md`）：

- **先查账本再开浏览器**：省掉补跑时无意义的"打开聊天"动作。
- **`history.json` 里的 `unknown` 也算"已处理"**：上游是 `reserve()` 先写账本再
  校验登录，中途失败会留下 `unknown`，后续补跑不会重发。接收侧复检能抓住并覆盖它。
- **发送侧确认是状态机**：气泡出现不算成功，要等转圈消失且稳定、或确认失败标记
  （Issue #11）。超时按"不确定"处理，宁可不重发也不重复。
- **群名匹配**：配置填基础名即可，代码接受 `名字(人数)` 后缀；私聊仍严格精确匹配。

## 5. 代码地图

### 上游（未改）

| 文件 | 作用 |
| --- | --- |
| `run.py` | 入口；有 `config/accounts.json` 走多账号，否则单账号 |
| `app/main.py` | 主流程：登录 → 逐目标打开 → 发消息 → 写结果 / 指标 / 通知 |
| `app/browser.py` | Chromium 启动、storage-state / cookie、登录与风控识别、trace |
| `app/douyin.py` | 会话搜索、精确名匹配、确认右侧聊天已打开 |
| `app/sender.py` | 文字 / 图片 / 原生表情发送；发送终态状态机 |
| `app/renewal.py` | 只读判断私聊今天是否已双方对话（火花已续） | 
| `app/marking.py` | 「标记文字发出即成功」判定，主任务与复检共用（本机新增） |
| `app/history.py` | `history.json` 防重复账本、`run.lock` 运行锁 |
| `app/config.py` | 解析 `.env` 与 `config.json`，校验消息 / 表情 / 间隔 |
| `app/selectors.py` | 所有 DOM 选择器集中在这里 |
| `app/privacy.py` | 真实昵称 → `好友NN` 的日志脱敏 |
| `app/notifier.py` | 钉钉 / 通用 Webhook（本机未启用） |
| `app/errors.py` | 错误分类（transient / permanent / auth / rate-limit）与重试策略 |
| `app/metrics.py` | `metrics.json` / `metrics_history.json` |
| `app/accounts.py`、`app/account_runner.py` | 多账号模式（本机未启用） |

### 本机新增（`scripts/`、`deploy/`）

| 文件 | 作用 | 会不会真发消息 |
| --- | --- | --- |
| `scripts/daily_run.sh` | 每日包装：跳过 / 休眠抑制 / 重试 / 通知 / 复检 | 会（转发给 run.py） |
| `scripts/verify_sent.py` | 接收侧复检；`--fix` 自动补发 | `--fix` 会 |
| `scripts/status.sh` | 最近 7 天覆盖、定时状态、凭证时间、高频错误 | **不会**（只读） |
| `scripts/list_chats.py` | 导出会话列表 + 火花状态 | **不会**（只读） |
| `scripts/find_target.py` | 改备注后用旧名字的片段反查当前联系人 | **不会**（只读） |
| `scripts/collect_nicknames.py` | 从个人主页采集昵称 / 抖音号 / sec_uid | **不会**（只读） |
| `scripts/make_config.py` | 由 `chats.json` 生成 `config.json` 并校验 | **不会** |
| `scripts/login_auto.py` | 扫码登录，自动检测成功并写 `storage-state.json` | **不会** |
| `scripts/install_launchd.sh` / `uninstall_launchd.sh` | 安装 / 卸载定时任务 | **不会** |
| `deploy/launchd/com.example.douyin-fire.plist` | launchd 模板（路径由安装脚本渲染） | — |

## 6. 文件与产物

| 路径 | 说明 | 进 git？ |
| --- | --- | --- |
| `config.json` | 目标名单 + 消息 + 间隔，本机核心配置 | ❌ |
| `storage-state.json` | 登录凭证，**等同账号密码** | ❌ |
| `.env` | 凭证路径、`HEADLESS=false`、`TRACE=true` | ❌ |
| `artifacts/history.json` | 防重复账本，**权威状态** | ❌ |
| `artifacts/result.json` | 最近一次结果（会被 dry-run 覆盖） | ❌ |
| `artifacts/logs/daily-run.log` | 包装脚本日志，**排查先看这个** | ❌ |
| `artifacts/logs/launchd.err.log` | launchd 层报错（TCC 类问题看这里） | ❌ |
| `artifacts/logs/verify-latest.json` | 最近一次复检详情 | ❌ |
| `artifacts/logs/failure-notified` | 今天是否已发过失败通知（可删） | ❌ |
| `artifacts/run.log` | 上游详细日志 | ❌ |
| `artifacts/screenshots/`、`artifacts/traces/` | 失败现场 | ❌ |
| `artifacts/run.lock` | 运行锁，残留会导致"已有任务在跑" | ❌ |
| `chats.json` / `chats.md` | 会话导出，**含全部真实昵称** | ❌ |
| `nicknames.json` | 采集到的昵称 / 抖音号 / sec_uid | ❌ |

## 7. 常用命令

```bash
cd <PROJECT_ROOT>

# 一眼看状态（只读，最常用；建议每周一次）
./scripts/status.sh

# 手动跑一次（今天已完成会跳过）
./scripts/daily_run.sh

# 强制执行 / 只验证不发送 / 不发通知
./scripts/daily_run.sh --force
./scripts/daily_run.sh --force --dry-run
./scripts/daily_run.sh --force --no-notify

# 接收侧复检（只读）
.venv/bin/python scripts/verify_sent.py
# 复检 + 自动补发（会真实发送）
.venv/bin/python scripts/verify_sent.py --fix

# 更换登录凭证（扫码，自动检测）
.venv/bin/python scripts/login_auto.py

# 重新导出会话 / 重新生成配置
.venv/bin/python scripts/list_chats.py --out chats.md
.venv/bin/python scripts/make_config.py

# 改备注后反查新名字 / 重新采集昵称（只读，安全）
.venv/bin/python scripts/find_target.py 旧名字
.venv/bin/python scripts/collect_nicknames.py --only 目标A,目标B

# 定时任务
launchctl print gui/$(id -u)/com.example.douyin-fire | head -40
launchctl kickstart -k gui/$(id -u)/com.example.douyin-fire
./scripts/install_launchd.sh    # 改过模板 / 挪过项目后重跑
./scripts/uninstall_launchd.sh

# 日志
tail -f artifacts/logs/daily-run.log

# 测试
.venv/bin/python -m pytest -q
```

## 8. 排障速查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 退出码 126，日志含 `Operation not permitted` | 项目被放回 `~/Desktop` | 移回 `<PROJECT_ROOT>` |
| 退出码 2，提示登录状态失效 | 凭证过期 | `scripts/login_auto.py` 重新扫码 |
| 某个目标反复「搜索不到目标好友」 | 改名 / 改备注 / 注销 / 群解散 | `scripts/find_target.py 旧名字` 反查，把新名字加进该目标 `aliases`（**别改 name**）；查不到再导出会话或删目标 |
| 提示「已有任务正在运行」 | 残留 `artifacts/run.lock` | 确认无进程后删除该文件 |
| 提示「发送历史损坏」 | `history.json` JSON 损坏 | 任务会主动停止；修复或备份后删除 |
| 提示「安全验证」 | 抖音风控 | 人工去 App / 网页完成验证，再重试 |
| 连续多天 0 条、`ERR_INTERNET_DISCONNECTED` | 机器断网 | 检查网络；网络类失败时钉钉/飞书同样发不出去 |
| `launchd.err.log` 为空但任务没跑 | 睡眠 / 关机错过时间点 | 唤醒后 launchd 会补跑睡眠期间的；关机的靠 `RunAtLoad` |

排查顺序：`daily-run.log` → `launchd.err.log` → `result.json` → `run.log` → `screenshots/`。

## 9. Git 与隐私边界

```
origin    https://github.com/<your-account>/douyin-auto-fire.git   # 自己的 fork
upstream  https://github.com/<upstream-owner>/douyin-auto-fire.git          # 原仓库
```

- 当前 `main` 比上游 `af0035f` 多 **5 个本机提交**，工作区干净。
- 上游更新：`git fetch upstream && git merge upstream/main`。冲突概率低（改动区域不重叠，
  只有 `.gitignore` 可能冲突）。**11/18 起例外**：为了支持 `aliases`，`app/models.py`、
  `app/config.py`、`app/main.py` 也有本机改动，上游若动到这三处（`Target` 定义、
  `_parse_target`、打开目标那段）需手工合并，别直接覆盖。
- 仓库是**公开**的。以下文件 `.gitignore` 已挡住，**永远不要 `git add -f`**：
  `storage-state.json`、`.env`、`config.json`、`chats.json` / `chats.md`、`nicknames.json`、`artifacts/`。
- 改动文档时同样脱敏，真实昵称一律写成 `目标NN` / `好友NN`。
- 提交前自查脚本见 `LOCAL_SETUP.md` 最后一节。

## 10. 延伸阅读

- `LOCAL_SETUP.md` —— 本机部署全记录：launchd 原理、TCC、复检判据、历史故障复盘、上游同步。
- `README.md` —— 上游功能说明。
- `docs/github-actions.md`、`docs/docker.md`、`docs/server.md`、`docs/windows.md` ——
  其他部署方式（本机没用，但改上游行为前值得对照）。
- `tests/` —— 246 个测试，覆盖配置、发送状态机、复检、脱敏、多账号、改名别名、昵称优先、标记成功规则等。
