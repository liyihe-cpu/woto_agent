# Woto Agent 技术报告

## 1. 项目概览

Woto Agent 是一个 Windows 本地采集工具，由桌面规划器和 WotoHub 浏览器采集器组成。用户在桌面窗口输入自然语言需求，系统将需求转换为结构化查询计划，再通过已登录的持久化浏览器执行查询，最终按平台和国家输出 CSV。

当前代码以“可运行主链路”为组织原则，调试探针、页面快照、日志、浏览器缓存和本地数据库均不进入版本库。

## 2. 目录结构

```text
.
├── agent_desktop/
│   ├── app.py                 # Tkinter 桌面入口、聊天区和任务控制
│   ├── agent_core.py          # 计划解析、附件读取、采集器启动
│   ├── tests.py               # 规划器与命令构造测试
│   ├── requirements.txt       # 桌面端依赖
│   └── README.md
├── wotohub_collector/
│   ├── main.py                # 浏览器会话、SQLite 检查点、基础采集能力
│   ├── vue_full_collector.py  # 生产采集编排、分页、去重、区间细分
│   ├── wotohub_page.js        # 页面上下文中的 Vue 数据提取逻辑
│   ├── keep_browser_open.py   # 启动并保持登录浏览器
│   ├── discover_options.py    # 从当前页面发现国家/语言选项
│   ├── export_country_summaries.py # 批次汇总导出
│   ├── config.example.yaml    # 配置模板
│   ├── config.yaml             # 本地运行配置
│   ├── tests.py                # 区间拆分和数据库测试
│   └── *.cmd                   # Windows 启动、续跑、状态命令
└── .gitignore
```

## 3. 核心执行链路

```text
自然语言/附件
      ↓
agent_desktop.agent_core.make_plan
      ↓
Plan.validate
      ↓
vue_full_collector.py
      ↓
main.Session 连接 127.0.0.1:9222
      ↓
wotohub_page.js 提取页面数据
      ↓
SQLite 检查点 + CSV 原子写入
      ↓
output/<platform>/<country>/*.csv
```

规划器优先使用 `.env` 中配置的模型 API；未配置 API 时使用本地规则，只接受显式 ISO 两位国家代码。采集器支持 `youtube`、`ins` 和 `tiktok`，国家顺序按计划保留，每个国家选择两种语言。

## 4. 主要模块职责

### 4.1 桌面端

- `app.py`：创建 Tkinter 窗口，接收文本和文件附件，异步运行计划生成与采集进程，并实时显示日志。
- `agent_core.py`：定义 `Plan` 数据结构；负责数量解析、平台识别、模型规划、本地回退、附件读取和采集命令生成。
- 附件支持文本、Markdown、CSV、JSON、YAML、图片以及 Excel 文件。

### 4.2 采集端

- `main.py`：管理浏览器上下文、配置加载、SQLite 批次与任务状态、页面检查、CSV 原子写入和断点续跑基础设施。
- `vue_full_collector.py`：按照平台、国家、语言、粉丝范围和发布时间执行任务；响应达到 10,000 条上限时自动缩小区间，遇到分页重复或响应不完整时记录状态。
- `wotohub_page.js`：在页面上下文中定位 Vue 数据，提取当前页 handle、总数和分页信息，避免依赖固定的后端内部编号。
- `keep_browser_open.py`：使用持久化 profile 启动 Edge，并开放 CDP 端口 `9222`。

## 5. 运行方式

### 安装

```powershell
cd wotohub_collector
python -m pip install -r requirements.txt
python -m playwright install chromium

cd ..\agent_desktop
python -m pip install -r requirements.txt
```

### 配置

复制 `agent_desktop/.env`（仅本地保存，不提交）并设置：

```ini
AGENT_API_URL=https://api.example/v1/chat/completions
AGENT_API_KEY=YOUR_API_KEY
AGENT_MODEL=YOUR_MODEL
```

采集参数在 `wotohub_collector/config.yaml` 中配置。命令行参数会覆盖 YAML 默认值。

### 启动

```powershell
cd wotohub_collector
.\open_browser.cmd
# 在浏览器中完成登录并保持窗口开启

cd ..\agent_desktop
python app.py
```

也可以直接运行采集器：

```powershell
cd wotohub_collector
python -u vue_full_collector.py --platform youtube --countries us br --follower-min 5000 --follower-max 10000 --span 5k --recent 30
```

常用辅助命令：

```powershell
python discover_options.py
python -u vue_full_collector.py --resume latest
python -u vue_full_collector.py --resume latest --retry-failed
python main.py status --batch BATCH_ID
python -m unittest -v
```

## 6. 数据与断点机制

- 运行状态保存在本地 `run_state_<platform>.json`。
- 任务明细和分页检查点保存在本地 `collector.sqlite3`。
- 结果写入 `output/<platform>/<country>/`。
- CSV 使用临时文件加替换方式写入，避免中断产生半截文件。
- 数据库、输出、日志、浏览器 `state/` 和探测快照均由 `.gitignore` 排除。

## 7. 区间拆分规则

粉丝上限采用开区间语义：`follower_min <= value < follower_max`。当查询结果达到平台返回上限时，采集器依次尝试更小跨度：

```text
1b → 10m → 1m → 100k → 1w → 5k → 1k → 500 → 250 → 二分
```

任务使用唯一 handle 去重；每个子任务完成后立即落盘，因此中断后可以从最近批次继续。

## 8. 清理范围与保留原则

已移除内容包括一次性页面探针、网络响应抓取脚本、调试选择器脚本、截图/HTML/JSON 快照、运行日志、SQLite 数据库、Python 字节码和浏览器运行产物。这些文件不参与桌面端到生产采集器的调用链。

保留内容包括生产入口、浏览器启动、计划解析、页面提取、配置发现、汇总导出、测试和 Windows 操作脚本，保证登录、采集、续跑、状态查询和 CSV 导出功能完整。

## 9. 验证清单

提交前应执行：

```powershell
cd agent_desktop
python -m unittest -v

cd ..\wotohub_collector
python -m unittest -v
python -m py_compile main.py vue_full_collector.py keep_browser_open.py discover_options.py export_country_summaries.py
```

完整端到端验证需要本地 Edge、Playwright、有效登录会话和可访问的 WotoHub 页面；仓库本身不保存账号凭据或登录状态。
