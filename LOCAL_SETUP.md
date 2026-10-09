# 本机 macOS 部署说明（自定义部分）

本项目基于 [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire)
（commit `af0035f`，2026-09-11）。**上游代码一字未改**，本文件描述在其之上为本机补的东西。

---

## ⚠️ 项目为什么不在桌面

项目实际位于 **`/Users/zrun/douyin-auto-fire`**。
`~/Desktop/douyin` 只是一个指向它的**符号链接**。

原因：**macOS TCC 禁止 launchd 启动的进程访问 `~/Desktop`。** 这不是猜测，是实测：

| 测试项（在 launchd 进程中执行） | 结果 |
| --- | --- |
| `ls ~/Desktop` | ❌ `Operation not permitted` |
| 读 `~/Desktop/douyin/config.json` | ❌ `Operation not permitted` |
| `ls ~`（家目录根） | ✅ 正常 |
| 读 `~/.zshrc` | ✅ 正常 |

项目放在桌面时，launchd 任务连脚本都 exec 不了，退出码 **126**：

```
shell-init: error retrieving current directory: getcwd: cannot access parent directories: Operation not permitted
bash: /Users/zrun/Desktop/douyin/scripts/daily_run.sh: Operation not permitted
```

**不要把它移回桌面**，除非你愿意给 `/bin/bash` 授予"完全磁盘访问权限"
（那是一个很宽的永久授权：任何 bash 脚本都能读你整块磁盘）。桌面上的符号链接
可以安全删除，只影响你自己访问的便利性，不影响定时任务。

> 注意：符号链接本身在桌面，所以**用桌面路径访问项目**时仍受 TCC 限制（GUI 应用有权限，
> 所以终端和 MyAgents 正常）。定时任务用的是真实路径，不受影响。

## 目录结构

```
/Users/zrun/douyin-auto-fire/      ← 项目真实位置
├── app/ config/ run.py ...        ← 上游原样，未改动
├── .venv/                         ← Python 3.12.13（uv 创建）
├── .env                           ← 环境变量（凭证路径、HEADLESS 等）
├── config.json                    ← 任务配置（16 个目标）★ 不进 git
├── storage-state.json             ← 扫码登录凭证 ★ 不进 git，等同于账号密码
├── chats.md / chats.json          ← 会话列表导出（含火花天数）
├── deploy/launchd/
│   └── com.zrun.douyin-fire.plist ← 定时任务模板（路径由安装脚本渲染）
├── scripts/
│   ├── login_auto.py              ← 【新增】扫码登录，自动检测成功
│   ├── login.py                   ← 上游的登录脚本（需在终端按回车，本机没用到）
│   ├── list_chats.py              ← 【新增】只读导出会话列表 + 火花状态
│   ├── make_config.py             ← 【新增】由 chats.json 生成 config.json
│   ├── daily_run.sh               ← 【新增】每日包装脚本（跳过/休眠/重试/通知/复检）
│   ├── verify_sent.py             ← 【新增】接收侧独立复检 + 自动补发
│   ├── install_launchd.sh         ← 【新增】安装定时任务
│   └── uninstall_launchd.sh       ← 【新增】卸载定时任务
└── artifacts/                     ← 日志/截图/结果（不进 git）
    ├── logs/daily-run.log         ← 包装脚本日志（排查先看这个）
    ├── logs/launchd.err.log       ← launchd 层的报错（TCC 类问题会出现在这）
    ├── logs/verify-latest.json    ← 最近一次复检的详细结果
    ├── logs/failure-notified      ← 当天是否已就失败发过通知（临时文件，可删）
    ├── result.json                ← 最近一次运行结果（每次运行覆盖；--dry-run 也会覆盖它）
    ├── history.json               ← 防重复账本（key = 任务:日期:目标:消息）★ 跳过判断以它为准
    ├── run.log                    ← 上游详细日志
    └── screenshots/               ← 失败时的截图
```

## 为什么多出这几个脚本

**为什么不用上游的定时方案？** 上游提供 GitHub Actions / Docker Cron / systemd Timer /
Windows 任务计划，**没有 macOS 的 launchd**。

**为什么需要 `daily_run.sh`？** 上游跳过"当天已发送成功"的消息时，**仍然会先打开每个
好友的聊天窗口**（去重判断在 `_open_target_with_retry` 之后）。四个补跑时间点如果都真的
启动浏览器，"打开聊天"会从 16 次/天变成 64 次/天 —— 反而是更明显的机器特征。

所以 `daily_run.sh` 在启动浏览器**之前**先读 `artifacts/history.json`：今天已全部成功就
直接退出，连浏览器都不开；有失败才真正执行，由去重逻辑只补发失败的那些。

**为什么读 `history.json` 而不是 `result.json`？** `result.json` 是每次运行都**覆盖**的
输出文件，任何一次 `--dry-run` 都会把它冲掉（2026-10-08 就真发生过），导致跳过失效、
白白开一次浏览器。而 `history.json` 只在**真实发送**时写入（上游有 `if not dry_run:` 守卫），
它同时也是去重真正依赖的那份账本 —— 用它判断"今天做完没有"才是权威的。

**为什么需要 `list_chats.py`？** 任务靠**昵称精确匹配**定位好友。实测踩过一次坑：
目标原名 `˶ᵒ ֊ ˂˶` 含**不间断空格 U+00A0**，抖音搜索接口不接受该字符（搜 0 条结果），
但结果里又原样带回 U+00A0 —— 即"输入的名字"和"要匹配的名字"必须不同，上游代码用同一个
字符串做这两件事，必然失败。解决办法是改名（见下），所以先用只读脚本导出真实昵称。

**为什么需要 `make_config.py`？** 直接从 `chats.json` 的原始字节生成配置，不经过手打，
避免不可见字符被手输破坏。

## 当前任务配置

- **16 个目标**（11 私聊好友 + 5 个群），来自"会话列表中带火花标识"的会话
- **消息**：文字「叮！自动续火花」+ 随机抖音原生表情（比心 / 开心，上游只提供这两个）
  - 注意：上游 `send_message()` 每次只发一种类型，所以"文字+表情"**是两条消息**，
    16 个目标共 **32 条**
- `prevent_duplicates: true` —— 四个补跑点靠它只补发失败的
- `send_interval_seconds: 3–8` —— 模拟真人的随机节奏

### 改过备注的目标

| 原名（已脱敏） | 改后的备注名（已脱敏） |
| --- | --- |
| 目标A（含 U+00A0） | 目标A′ |
| 目标B | 目标B′ |
| 目标C | 目标C′ |
| 目标D | 目标D′ |

**在抖音里改备注会改变搜索和显示的名称。** 如果你以后再改备注，配置会失效 ——
重跑 `list_chats.py` + `make_config.py` 即可。

## 定时安排

| 时间 | 作用 |
| --- | --- |
| `00:10` | 首选（Mac 恰好醒着才跑） |
| `09:30` | 主力兜底 |
| `15:00` | 二次兜底 |
| `20:00` | 末班车，距自然日结算还剩 4 小时 |
| 每次登录 | `RunAtLoad`，补"关机错过全部时间点"的情况 |

launchd 的两条关键行为（来自 `launchd.plist(5)` 手册）：

- **睡眠期间**错过的定时任务，会在唤醒后合并补跑一次
- **关机期间**错过的定时任务，**不会**补跑 —— 这就是 `RunAtLoad` 存在的理由

所以 00:10 大概率不会在 00:10 执行（MacBook 合盖睡眠），而是你早上掀盖子时补跑。
这不影响结果：当天照样完成，且 09:30 那个点仍在。

## 已实测验证的行为

2026-10-07 在本机实际跑过，不是推测：

| 场景 | 结果 | 验证方式 |
| --- | --- | --- |
| 锁屏状态下执行 | ✅ **完全正常** | 专门搭了个临时 LaunchAgent，把执行**当时**的 `CGSSessionScreenIsLocked` 写进日志。36.7 秒全程 `true`，16/16 目标定位成功，退出码 0 |
| launchd 拉起有窗口的 Chromium | ✅ 正常 | 同上，以及一次单独的自检任务（16/16，38.7 秒） |
| 从 `~/Desktop` 运行 | ❌ **完全不可用** | 退出码 126，见本文开头 |
| 重复触发同一天 | ✅ 正确跳过 | `daily_run.sh` 读 history.json 后直接退出，不开浏览器。抽掉 1 条记录后确认不再跳过 |
| launchd 触发 macOS 通知 | ✅ 能正常弹出 | 由 launchd 启动临时任务发两条通知，用户确认屏幕上两条都看到了 |

**「锁屏」与「重启后停在 FileVault 解锁界面」是两回事**：前者用户会话仍在，任务照跑；
后者尚无用户会话，任务不会运行，需等登录后由 `RunAtLoad` 补上。

**未验证**：合盖休眠 → 唤醒后能否合并补跑（`launchd.plist(5)` 手册称会，但本机未实测）。
即使不成立，最坏结果也只是当天延后到 09:30 执行，不会漏掉一天。

### 2026-10-08 00:18 那次运行的实际观察

第一次无人值守运行**成功了**（16/16，32 条消息），但**耗时 1 小时 36 分**（正常约 10 分钟）：

```
00:18:57 开始 → 00:19:45 好友02 ✓
00:32:05 好友03   ← 中间隔了 12 分钟
00:49:25 好友04   ← 隔了 17 分钟
01:05:59 好友05   ← 隔了 16 分钟
...（平均每条消息 179.7 秒，最慢一条 1017 秒）
01:54:48 结束
```

原因是 Mac 反复进入休眠、进程被挂起又恢复，进度一跳一跳。

**已采取的缓解措施**：`daily_run.sh` 现在用 `caffeinate -i` 包住任务，阻止**空闲休眠**。
以及新增的"上一次运行仍在进行则跳过"检查——如果某次跑得太久撞到了下一个时间点，
上游会抛 `AlreadyRunningError`（退出码 2），那会被误报成"凭证失效"，现在会被正确识别为跳过。

**注意**：`caffeinate` 挡不住**合盖休眠**（clamshell sleep）。如果半夜盖子合着，
任务仍会被挂起——只是结果不受影响，因为最终会跑完，且后续时间点会兜底。

## 失败通知

用的是 macOS 原生通知（`osascript -e 'display notification …'`），不需要钉钉或任何外部服务。
**已于 2026-10-08 实测**：launchd 触发的通知能正常弹出，两条测试通知用户都确认看到。

设计原则是**不吵人**——半夜把你叫醒毫无意义，因为 09:30 会自动重试：

| 情况 | 通知 | 声音 |
| --- | --- | --- |
| 今天还有补跑时间点，本次失败 | "本次失败，今天 HH:MM 会自动重试" | 静默 |
| 今天**最后一次机会**也失败 | "今天可能没续上，详见 …" | **Basso（响铃）** |
| 今天早些时候失败过、后来自动恢复了 | "已自动恢复，无需处理" | 静默 |
| 一切正常 / 重复触发被跳过 | 不发通知 | — |

所以**失败的日子最多 4 条通知**（每个时间点一条），**成功的日子一条都没有**。

用 `artifacts/logs/failure-notified` 记录当天是否已就失败发过通知，
用来判断要不要补发"已自动恢复"。这个文件是临时的，可以随时删。

调试时可以关掉通知：`./scripts/daily_run.sh --no-notify`。
如果看到日志里出现 `[警告] macOS 通知发送失败`，说明被隐私设置拦了。

## 复检与自动补发（verify_sent.py）

主任务报"发送成功"依据的是**发送时**的页面状态（气泡出现、无重试标记）——那是
**发送侧**的自我判断。`verify_sent.py` 做的是**接收侧**的独立核对：回到每个聊天里，
看最新一条来自我的标记消息，其**时间分隔符**是不是今天。

成功的一天里它的输出长这样：

```
[ 9/16] ✗ 今天没发   时间=昨天 01:34     目标09
[16/16] ✗ 今天没发   时间=昨天 01:54     目标16
```

### 判据（来自实测的 DOM 结构）

`[data-index]` 的文档顺序 = 从新到旧；**时间分隔符挂在每个时间组的最新那条上**：

```
idx=1  我  TS=13分钟前   "13分钟前 叮！自动续火花"   ← 今天 ✓
idx=8  我  TS=昨天 20:51                            ← 昨天 ✗
```

所以对"最新一条含标记文本的、来自我的消息"，往上找它所属时间组的时间，
再用 `_classify()` 判断：`刚刚` / `N分钟前` / `N小时前` / `HH:MM` → 今天；
`昨天` / `前天` / `星期X` / `MM/DD` / `YYYY/MM/DD` → 不是今天。

### 三步闭环

```
第一轮  复检 16 个目标  →  列出"今天没发"的
第二轮  补发这些目标     →  用与主任务完全相同的 send_message，并写入 history.json
第三轮  重新复检补发的   →  确认真的发出去了
```

`daily_run.sh` 在**主任务报全部成功之后**自动跑这套（Dry Run 除外）：

- 补发成功 → 静默通知"复检补发了 N 个遗漏"
- 仍有遗漏 → **响铃**通知，并写 `artifacts/logs/verify-latest.json`

### 为什么必须有它 —— 2026-10-09 的真实故障

凌晨那次在 15 个目标中的第 9 个被**误判成凭证失效**而中断（详见下节），只发出 8 个。
更阴险的是那第 9 个目标：上游代码是**先 `history.reserve()` 再 `verify_login()`**，
检查失败就抛异常，于是账本里留下一条 `status="unknown"` 的记录。
而 `prevent_duplicates` 只看"key 存在不存在"——**`unknown` 也算存在**——
所以后续所有补跑点都会永久跳过这条消息，今天这一半就静默烂掉了。

复检正好能抓住它（找的是文字标记，找不到今天的 → 判定遗漏 → 补发时 `reserve()` 覆盖掉
那条 `unknown`）。2026-10-09 实测：复检出 8 个遗漏，全部补发成功，账本最终
16 目标 × 2 条 = 32 条全 `success`。

### 手动用法

```bash
# 只复检，报告给人工看（不发送任何消息）
.venv/bin/python scripts/verify_sent.py

# 复检 + 自动补发 + 重新复检（会真实发送）
.venv/bin/python scripts/verify_sent.py --fix

# 只查某几个目标
.venv/bin/python scripts/verify_sent.py --only 目标16,目标13

# 结果写 JSON
.venv/bin/python scripts/verify_sent.py --json artifacts/logs/verify-latest.json
```

退出码：`0` = 全部确认；`1` = 有遗漏；`2` = 运行出错。

## 瞬时抖动防护

2026-10-09 00:21 的故障根因：打开群聊时页面切换的**瞬间**，某个含"登录后"的元素
短暂可见，撞上只有 3 秒的登录复检窗口（`verify_login(page, timeout_ms=3_000)`），
被 `LOGIN_REQUIRED_MARKERS` 误判成登录失效。而 `AuthenticationError` 会**中断整个任务**，
16 个目标只发了 8 个就停了。

当时的截图证据：页面完全正常（会话列表、搜索框、输入框都在），**根本没掉登录**。

诊断补充：在健康页面上逐个测过，所有 `LOGIN_REQUIRED_MARKERS` 和 `RISK_MARKERS`
都是 **0 匹配**，所以这是**偶发竞态**，不是稳定复现的——无法靠改选择器根治
（`text=登录后` 是子串匹配，但当时到底哪个元素命中无法复现）。

**采取的防护**（`daily_run.sh`，零上游改动）：退出码 2 时，如果**今天已有成功发送记录**，
说明"登录本来是好的、只是中途抖了一下"——等 60 秒重跑一次。真正的凭证过期会在
**第一个**目标就失败，不会有部分成功记录，所以不会被误触发。代价最多是多开一次浏览器。

**未采用**：修改 `app/selectors.py` 把 `text=登录后` 改成精确匹配。因为无法复现，
改了也不确定能修好，却要永久偏离上游。

### 怎么自己复现锁屏测试

```bash
# 临时任务：dry-run，产物写 /tmp，绝不碰真实 result.json
cat > /tmp/locktest-job.sh <<'SH'
#!/bin/bash
J=/tmp/locktest-job.out
echo "JOB_START $(date '+%F %T')" > "$J"
echo "LOCK_AT_START: $(ioreg -n Root -d1 -a | plutil -convert json -o - - | grep -o '"CGSSessionScreenIsLocked"[^,}]*' || echo ABSENT)" >> "$J"
cd /Users/zrun/douyin-auto-fire && .venv/bin/python run.py --dry-run >> "$J" 2>&1
echo "DRYRUN_EXIT=$?" >> "$J"
echo "LOCK_AT_END: $(ioreg -n Root -d1 -a | plutil -convert json -o - - | grep -o '"CGSSessionScreenIsLocked"[^,}]*' || echo ABSENT)" >> "$J"
SH
chmod +x /tmp/locktest-job.sh
```

plist 里加 `EnvironmentVariables` → `ARTIFACTS_DIR=/tmp/locktest-artifacts`，
这样 result.json 和 history.json 都写到 /tmp，**不会污染真实的防重复账本**。
然后锁屏、`launchctl kickstart -k gui/$(id -u)/<label>`、解锁看 `/tmp/locktest-job.out`。

## 常用命令

```bash
cd /Users/zrun/douyin-auto-fire

# 手动跑一次（今天已完成会跳过）
./scripts/daily_run.sh

# 强制跑，忽略"今天已完成"
./scripts/daily_run.sh --force

# 只验证登录和好友定位，不发送任何消息
./scripts/daily_run.sh --force --dry-run

# 本次不发 macOS 通知（调试用）
./scripts/daily_run.sh --force --no-notify

# 独立复检今天有没有漏发（不发任何消息）
.venv/bin/python scripts/verify_sent.py

# 复检 + 自动补发漏掉的（会真实发送）
.venv/bin/python scripts/verify_sent.py --fix

# 重新导出会话列表（换好友、改备注之后用）
.venv/bin/python scripts/list_chats.py --out chats.md

# 依据会话列表重新生成配置
.venv/bin/python scripts/make_config.py

# 凭证过期后重新登录（扫码，自动检测成功）
.venv/bin/python scripts/login_auto.py

# 查看定时任务状态
launchctl print gui/$(id -u)/com.zrun.douyin-fire | head -40

# 看日志
tail -f artifacts/logs/daily-run.log

# 立即触发一次定时任务（测试用）
launchctl kickstart -k gui/$(id -u)/com.zrun.douyin-fire

# 安装 / 卸载定时任务
./scripts/install_launchd.sh
./scripts/uninstall_launchd.sh
```

## 出问题先看哪里

排查顺序：`artifacts/logs/daily-run.log` → `artifacts/logs/launchd.err.log`
→ `artifacts/result.json` → `artifacts/run.log` → `artifacts/screenshots/`。

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 退出码 126，日志含 `Operation not permitted` | 项目被移回了 `~/Desktop` | 移回 `~/douyin-auto-fire` |
| 退出码 2，提示登录状态失效 | Cookie / storage-state 过期 | 重跑 `scripts/login_auto.py` |
| 提示"安全验证" | 抖音风控 | 手动去 App 或网页完成验证 |
| 某个目标"搜索不到目标好友" | 昵称对不上、对方改名或改备注 | `list_chats.py` 重新导出后 `make_config.py` |
| 提示"已有任务正在运行" | 残留 `artifacts/run.lock` | 确认无进程后删掉该文件 |
| 提示"发送历史损坏" | `artifacts/history.json` 损坏 | 该文件防重复发，损坏时任务会主动停止 |

## ⚠️ 两个已知前提

1. **火花需要双向互动。** 抖音火花要求双方在当天**都**发过消息才计入天数。本项目只能
   保证你这一半。当前文案「叮！自动续火花」是通知式短句，**不诱发对方回复** ——
   这是已知并接受的取舍。如果几天后火花没增长，说明对方没回，需要换提问式文案。

2. **`降级到位置兜底` 有风险。** 原生表情定位有一个 `fallback_index` 按下标盲点的兜底
   机制；抖音表情面板改版时它可能点错表情。上游目前只提供"比心""开心"两个表情。

## 更新上游代码

上游代码未被修改，可以直接拉取更新：

```bash
cd /Users/zrun/douyin-auto-fire
git stash                # 如有本地改动
git pull                 # 只涉及 app/ config/ run.py 等上游文件
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium
```

`scripts/` 下新增的脚本不受影响。注意：`.env`、`config.json`、`storage-state.json`
都在 `.gitignore` 里，`git pull` 不会覆盖它们。
