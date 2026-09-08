# WotoHub Handle 采集 Agent

一个本地桌面聊天窗口：输入自然语言采集需求，确认后自动生成检索方案、启动 WotoHub 采集器，并把实时日志显示在聊天区。

## 运行前准备

需要 Windows、Python 和 Microsoft Edge。首次安装依赖：

```powershell
cd D:\woto_highflexi_agent\agent_desktop
python -m pip install -r requirements.txt
```

采集器使用同级目录 `wotohub_collector` 中的 Playwright 与 Edge 登录态。如尚未安装其依赖，请执行：

```powershell
python -m pip install -r ..\wotohub_collector\requirements.txt
python -m playwright install chromium
```

## 配置模型

编辑本目录的 `.env`。SiliconFlow 的默认接口与模型已预填，只需填写密钥：

```ini
AGENT_API_URL=https://api.siliconflow.cn/v1/chat/completions
AGENT_API_KEY=sk-你的SiliconFlow密钥
AGENT_MODEL=deepseek-ai/DeepSeek-V4-Flash
```

`.env` 不会提交到 Git；可用 `.env.example` 作为模板。未配置模型时，本地规则只接受显式 ISO 两位国家代码（例如 `gb fr de`）；国家中文名、简称和地区表达由模型负责转换。

## 启动全流程

1. 打开一个 PowerShell，启动专用浏览器：

   ```powershell
   cd D:\woto_highflexi_agent\wotohub_collector
   .\open_browser.cmd
   ```

2. 在出现的 Edge 窗口中登录 WotoHub，保持该窗口打开。采集器通过本机 `127.0.0.1:9222` 连接这个浏览器会话。

3. 回到另一个 PowerShell，启动桌面 Agent：

   ```powershell
   cd D:\woto_highflexi_agent\agent_desktop
   python app.py
   ```

4. 在唯一的聊天窗口输入需求。例如：

   ```text
   YouTube 美国，粉丝 5000 到 5500，近 30 天，初始跨度 500
   ```

5. 点击 **确认**。窗口会依次显示：

   ```text
   正在生成检索方案…
   平台、国家、粉丝范围、跨度、发布时间与解析方式
   检索方案正在运行…
   实时采集日志
   ```

6. 任务结束后，“确认”按钮恢复，可输入下一条需求。

## 输入与附件

- 支持平台：`youtube`、`ins`（Instagram）、`tiktok`。
- 模型会将国家中文名、简称、英文名和地区表达转换为 ISO 两位代码，并保留你的输入顺序作为采集优先级。
- 粉丝上限为排除上限：`5000 到 5500` 表示 `5,000–5,499`。
- 发布时间可写“不限”、`近30天`、`近60天` 或 `近90天`。
- 可直接把文件拖入聊天区或输入框；也可在资源管理器复制文件后，在输入框按 `Ctrl+V`。普通文本粘贴不受影响。
- 支持 `.txt`、`.md`、`.csv`、`.xlsx`、`.xls`、`.json`、`.yaml`、`.yml`、`.png`、`.jpg`、`.jpeg`、`.webp`。

## 结果位置

CSV 会按平台、国家与语言写入：

```text
D:\woto_highflexi_agent\wotohub_collector\output\<platform>\<country>\
```

例如：

```text
output\youtube\us\youtube_us_en_5000-5499_recentdays30.csv
```

运行状态在 `wotohub_collector\run_state_<platform>.json`，里面有批次 ID、状态与任务汇总。

## 已验证的端到端运行

本机已完成一次 Agent 调用链验证：

```text
条件：YouTube / US / 5,000–5,499 粉丝 / 近 30 天 / 跨度 500
批次：20260908_111551_d72eae
状态：complete（2 个语言任务）
输出：英语 2,898 个 handle；西班牙语 159 个 handle
```

## 排错

- 出现“Open WotoHub in the persistent browser first”：先执行 `open_browser.cmd`，登录后保持 Edge 窗口打开。
- 出现 API 配置错误：检查 `.env` 三项是否均已填写，并重启 `python app.py`。
- 需要恢复中断的采集：在 `wotohub_collector` 目录运行对应的 `resume_latest.cmd`、`resume_ins_latest.cmd` 或 `resume_tiktok_latest.cmd`。

## 本地测试

```powershell
cd D:\woto_highflexi_agent\agent_desktop
python -m unittest -v
```
