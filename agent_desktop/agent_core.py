"""Planning and execution primitives for the local collector agent."""
from __future__ import annotations

import base64
import csv
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent
COLLECTOR_ROOT = APP_ROOT.parent / "wotohub_collector"


def load_env_file(path: Path = APP_ROOT / ".env") -> None:
    """Load local KEY=VALUE settings without requiring a shell profile."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip("\"'")


load_env_file()
VALID_PLATFORMS = ("youtube", "ins", "tiktok")
VALID_SPANS = ("1b", "10m", "1m", "100k", "1w", "5k", "1k", "500", "250")
SPAN_VALUES = {"1b": 1_000_000_000, "10m": 10_000_000, "1m": 1_000_000,
               "100k": 100_000, "1w": 10_000, "5k": 5_000, "1k": 1_000, "500": 500, "250": 250}
PLANNER_INSTRUCTIONS = """You are the planning brain of a WotoHub handle collection desktop agent.
Convert the FULL conversation into one execution plan for the collector.
Return exactly one valid JSON object, with no Markdown and no additional properties:
{"platform":"youtube|ins|tiktok","countries":["iso-code"],"follower_min":0,"follower_max":1,"span":"5k","recent":30}

Program logic and input standard:
- The collector searches creator handles by platform, creator country, follower range,
  initial query span, and publication recency.
- Supported platforms are youtube, ins (Instagram), and tiktok.
- `countries` is an ordered list of lowercase ISO 3166-1 alpha-2 codes. Preserve order.
- `follower_min` is inclusive and `follower_max` is exclusive.
- The collector runs two suitable languages per country and writes CSV output by platform/country.
- Requirements may arrive over multiple turns. Merge every turn; later corrections override
  earlier values, while earlier fields remain active.

Rules:
1. Translate every country name, abbreviation, colloquial grouping, and language in the full conversation yourself. Examples: 英法德意西美 -> ["gb","fr","de","it","es","us"], 英美 -> ["gb","us"].
2. Preserve the user's stated country order exactly. That array order is the collection priority. For example, “英法德意西” becomes ["gb","fr","de","it","es"].
3. `platform` is only `youtube`, `ins`, or `tiktok` (`instagram` becomes `ins`).
4. `follower_min` is inclusive and `follower_max` is exclusive. Convert quantities such as 2k, 300k, 万, 百万, and 亿 into integers.
5. `span` is exactly one of `1b`, `10m`, `1m`, `100k`, `1w`, `5k`, `1k`, `500`, `250`. `1w` means 10,000 followers. Use the explicit request; otherwise use `5k`.
6. `recent` is exactly 0, 30, 60, or 90. Use 0 for unlimited time and 30 when absent.
7. The collector runs two suitable languages per country, preserves this country order, and automatically refines a range only when a query reaches 10,000 results: 1w -> 5k -> 1k -> 500 -> 250 -> binary split. Do not generate child tasks; output only the requested initial plan.
"""


@dataclass
class Plan:
    platform: str = "youtube"
    countries: list[str] | None = None
    follower_min: int = 5_000
    follower_max: int = 10_000
    span: str = "5k"
    recent: int = 30
    source: str = "local"

    def validate(self) -> "Plan":
        self.platform = "ins" if self.platform == "instagram" else self.platform.lower()
        if self.platform not in VALID_PLATFORMS:
            raise ValueError(f"不支持的平台：{self.platform}")
        self.countries = list(dict.fromkeys(x.lower() for x in (self.countries or []) if re.fullmatch(r"[a-z]{2}", x.lower())))
        if not self.countries:
            raise ValueError("没有识别到国家代码，请补充国家或地区。")
        if self.follower_min < 0 or self.follower_max <= self.follower_min:
            raise ValueError("粉丝范围必须满足上限大于下限。")
        if self.span not in VALID_SPANS:
            raise ValueError(f"跨度必须为：{', '.join(VALID_SPANS)}")
        if self.recent not in (0, 30, 60, 90):
            raise ValueError("发布时间只能为不限、30、60 或 90 天。")
        return self

    def summary(self) -> str:
        recent = "不限" if self.recent == 0 else f"最近 {self.recent} 天"
        return (f"平台：{self.platform}\n国家（按优先顺序）：{' → '.join(self.countries or [])}\n"
                f"粉丝范围：{self.follower_min:,}–{self.follower_max - 1:,}\n"
                f"初始跨度：{self.span}\n发布时间：{recent}\n每国语言：2 种")


def amount(value: str) -> int:
    number, unit = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(亿|千万|百万|万|w|b|m|k)?\s*", value.lower()).groups()
    scale = {None: 1, "k": 1_000, "w": 10_000, "m": 1_000_000, "b": 1_000_000_000,
             "万": 10_000, "百万": 1_000_000, "千万": 10_000_000, "亿": 100_000_000}[unit]
    return int(float(number) * scale)


def _numbers(text: str) -> list[int]:
    matches = re.findall(r"\d+(?:\.\d+)?\s*(?:千万|百万|亿|万|[kmbw])?", text.lower())
    return [amount(item) for item in matches]


def local_plan(text: str) -> Plan:
    """Offline fallback: accepts only explicit ISO country codes.

    Natural-language country translation belongs to the configured planning model.
    """
    lower = text.lower()
    countries = list(dict.fromkeys(re.findall(r"(?<![a-z])[a-z]{2}(?![a-z])", lower)))
    platform = "tiktok" if "tiktok" in lower else "ins" if ("instagram" in lower or "ins" in lower) else "youtube"
    recent = 0 if ("不限" in text or "全部时间" in text) else next((n for n in (90, 60, 30) if str(n) + "天" in text), 30)
    span = next((name for name in VALID_SPANS if name in lower), None)
    if not span:
        if "十亿" in text: span = "1b"
        elif "一千万" in text: span = "10m"
        elif "一百万" in text: span = "1m"
        elif "十万" in text: span = "100k"
        else: span = "5k"
    values = _numbers(text)
    lo, hi = (values[0], values[1]) if len(values) >= 2 else (5_000, 10_000)
    return Plan(platform, countries, lo, hi, span, recent, "local").validate()


def attachment_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".json", ".yaml", ".yml"):
        return path.read_text(encoding="utf-8", errors="replace")[:20_000]
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
            return "\n".join(" | ".join(row) for _, row in zip(range(200), csv.reader(f)))
    if suffix in (".xlsx", ".xls"):
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = []
        for row in book.active.iter_rows(values_only=True):
            rows.append(" | ".join("" if value is None else str(value) for value in row))
            if len(rows) == 200: break
        return "\n".join(rows)
    return f"附件文件：{path.name}（{suffix or '无扩展名'}）"


def _api_plan(text: str, files: list[Path], conversation: list[tuple[str, str]] | None = None) -> Plan:
    url, key, model = os.getenv("AGENT_API_URL"), os.getenv("AGENT_API_KEY"), os.getenv("AGENT_MODEL")
    if not (url and key and model):
        raise RuntimeError("未配置 AGENT_API_URL、AGENT_API_KEY、AGENT_MODEL")
    turns = conversation or [("user", text)]
    transcript = "\n".join(f"{role}: {message}" for role, message in turns)
    content: list[dict[str, Any]] = [{"type": "text", "text":
        "Conversation transcript (use all turns):\n" + transcript}]
    for path in files:
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else f"image/{path.suffix.lower()[1:]}"
            data = base64.b64encode(path.read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        else:
            content.append({"type": "text", "text": f"\n附件 {path.name}:\n{attachment_text(path)}"})
    payload = json.dumps({"model": model, "messages": [{"role": "system", "content": PLANNER_INSTRUCTIONS}, {"role": "user", "content": content}], "temperature": 0,
                          "response_format": {"type": "json_object"}}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.load(response)
    raw = body["choices"][0]["message"]["content"]
    return Plan(**json.loads(raw), source="api").validate()


def make_plan(text: str, files: list[Path] | None = None,
              conversation: list[tuple[str, str]] | None = None) -> Plan:
    files = files or []
    if os.getenv("AGENT_API_URL") and os.getenv("AGENT_API_KEY") and os.getenv("AGENT_MODEL"):
        return _api_plan(text, files, conversation)
    merged = text + "\n" + "\n".join(attachment_text(path) for path in files if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"))
    return local_plan(merged)


def collector_command(plan: Plan) -> list[str]:
    plan.validate()
    script = COLLECTOR_ROOT / "vue_full_collector.py"
    if not script.exists():
        raise FileNotFoundError(f"找不到采集器：{script}")
    return [sys.executable, "-u", str(script), "--platform", plan.platform, "--countries", *plan.countries,
            "--follower-min", str(plan.follower_min), "--follower-max", str(plan.follower_max),
            "--span", plan.span, "--recent", "all" if plan.recent == 0 else str(plan.recent),
            "--preserve-country-order"]


def start_collector(plan: Plan) -> subprocess.Popen[str]:
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen(collector_command(plan), cwd=COLLECTOR_ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=child_env)


def plan_json(plan: Plan) -> str:
    return json.dumps(asdict(plan), ensure_ascii=False, indent=2)
