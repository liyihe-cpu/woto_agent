# WotoHub 采集器

主程序是 `vue_full_collector.py`。它连接已打开且已登录的浏览器，按“平台 × 国家 × 每国两种语言 × 粉丝区间”采集 handle，并把达到 10,000 条上限的区间按既有规则继续细分。

## 首次准备

```powershell
pip install -r requirements.txt
```

1. 双击 `start_login.cmd`，在打开的浏览器中登录 WotoHub。
2. 双击 `open_browser.cmd`，并保持该浏览器窗口打开。
3. 运行一次 `python discover_options.py`，生成最新的 `discovered_options.json`。
4. 可先做一次小范围验证：

```powershell
python -u vue_full_collector.py --verify
```

## 打开专用浏览器（每次采集前）

采集器不会自行新开浏览器，它会连接运行在本机 `127.0.0.1:9222` 的专用浏览器。因此每次运行采集命令前，先在本目录执行：

```powershell
.\open_browser.cmd
```

等待浏览器窗口出现并进入 WotoHub。第一次使用时，在该窗口内完成登录；之后登录状态会保存在 `state\profile`。浏览器窗口必须保持打开，再另开一个 PowerShell 窗口执行采集命令。

可用下面命令确认 9222 已就绪；返回 JSON（含 `webSocketDebuggerUrl`）即表示浏览器已打开：

```powershell
Invoke-RestMethod http://127.0.0.1:9222/json/version
```

若没有返回内容，先关闭所有 Edge/Chrome 窗口，再重新运行 `.\open_browser.cmd`。不要关闭由该脚本打开的 WotoHub 浏览器窗口。

## 运行

以下命令会采集 YouTube、美国和巴西、粉丝 5,000 到 9,999、最近 30 天发布的账号：

```powershell
python -u vue_full_collector.py --platform youtube --countries us br --follower-min 5000 --follower-max 10000 --span 5k --recent 30
```

平台：

```powershell
python -u vue_full_collector.py --platform youtube
python -u vue_full_collector.py --platform ins
python -u vue_full_collector.py --platform tiktok
```

国家可输入多个两位代码，空格或逗号都可分隔；`--all-countries` 会遍历所有已发现国家。每个国家会按既有逻辑自动选取两种可用语言。

```powershell
python -u vue_full_collector.py --countries us,br,jp
python -u vue_full_collector.py --all-countries
```

发布时间窗口支持不限、30、60、90 天：

```powershell
python -u vue_full_collector.py --recent all
python -u vue_full_collector.py --recent 60
python -u vue_full_collector.py --recent 90
```

粉丝区间下限包含、上限不包含。因此 `--follower-min 5000 --follower-max 10000` 覆盖 5,000–9,999。初始跨度可用：`1b`（十亿）、`10m`、`1m`、`100k`、`1w`（一万）、`5k`、`1k`、`500`、`250`。

```powershell
python -u vue_full_collector.py --follower-min 10000 --follower-max 10000000 --span 1m
```

区间结果达到 10,000 条时，分裂规则不变：`1b → 10m → 1m → 100k → 1w → 5k → 1k → 500 → 250 → 二分`；子任务继承平台、国家、语言和发布时间窗口。

## 配置默认值

不想每次写命令行参数时，编辑 `config.yaml`：

```yaml
filters:
  follower_min: 5000
  follower_max: 10000  # 上限不包含
  span: 5000
  countries: [us, br, jp]  # 或 all
  recent_days: 30          # 0=不限；也可为 30、60、90
```

命令行参数会覆盖配置默认值。

## 续跑与输出

```powershell
python -u vue_full_collector.py --resume latest
python -u vue_full_collector.py --resume latest --retry-failed
python main.py status --batch BATCH_ID
```

CSV 输出在 `output/<平台>/<国家>/`；文件名包含粉丝区间和发布时间，例如 `youtube_us_en_5000-9999_recentdays30.csv`。不限发布时间时文件名使用 `recentall`。
