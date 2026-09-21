"""LLM-directed, count-driven follower-range collection for WotoHub."""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_desktop"))
from agent_core import ask_agent_json  # noqa: E402
from collector_runtime import load_runtime  # noqa: E402


TOTAL_HEADER = ("国家", "平台", "语言", "粉丝量区间", "handle")
_total_rows: dict[Path, set[tuple[str, str, str, str, str]]] = {}
DAILY_QUOTA_MARKERS = ("超过每日访问次数", "daily access limit", "daily quota")


class DailyQuotaExceeded(RuntimeError):
    """The source account has reached its WotoHub daily request allowance."""


DAILY_QUOTA_MARKERS = DAILY_QUOTA_MARKERS + ("本地每日请求保护阈值",)


def platform_total_path(platform: str) -> Path:
    """One user-facing export per platform; countries and languages are rows."""
    return HERE / "output" / f"{platform}_总表.csv"


def append_platform_rows(platform: str, country: str, language: str,
                         follower_range: str, handles: list[str]) -> tuple[Path, int]:
    path = platform_total_path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = _total_rows.get(path)
    if seen is None:
        seen = set()
        if path.exists():
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.reader(stream):
                    if tuple(row) != TOTAL_HEADER and len(row) == len(TOTAL_HEADER):
                        seen.add(tuple(row))
        _total_rows[path] = seen
    rows = [(country, platform, language, follower_range, str(handle).strip())
            for handle in handles if str(handle).strip()]
    new_rows = [row for row in rows if row not in seen]
    if new_rows:
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            if needs_header:
                writer.writerow(TOTAL_HEADER)
            writer.writerows(new_rows)
        seen.update(new_rows)
    return path, len(new_rows)


SYSTEM = """You are the decision agent for an adaptive WotoHub creator search.
Return exactly one JSON object and nothing else:
{"action":"probe|collect|done","follower_min":INTEGER,"follower_max":INTEGER,"reason":"..."}

You receive the original request, target range, current remaining range, the
current probe, complete probe history, and completed ranges.

Rules:
- The first probe for a remaining range is its complete range. If its real
  count is below hard_cap, immediately return collect for that identical
  range. Never probe an already safe range again just to optimize its count.
- When the current probe reaches hard_cap, return probe. Keep follower_max
  identical and choose a strictly higher follower_min yourself. Use observed
  density, range width, probe history, completed ranges, and the fact that
  lower-follower creators are normally denser than high-follower creators.
  Do not use midpoint splits, fixed increments, fixed percentage changes, or
  pre-defined follower buckets.
- Aim for a wide range likely below hard_cap, but hard_cap is strict.
- Once a high-follower range is safe, collect it immediately. The runtime will
  then expose the remaining lower-follower range for its own complete first
  probe. Never propose an overlapping or already completed range.
- The completion criterion is full export coverage, not a sample size. Return
  done only when no remaining range exists and every value in target_range is
  covered by a collected safe range.
"""

STATE_VERSION = "tail-agent-v3"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    id: str
    low: int
    high: int
    scope_low: int
    status: str = "pending"
    count: int | None = None
    parent_id: str | None = None
    reason: str | None = None
    collection_page: int = 1

    def public(self) -> dict[str, Any]:
        return asdict(self) | {"range": f"{self.low:,}-{self.high:,}"}


class ModelLoop:
    def __init__(self, args: argparse.Namespace, collector: Any, state_path: Path, runtime: dict[str, Any] | None = None) -> None:
        self.args, self.collector, self.state_path = args, collector, state_path
        self.runtime = runtime or load_runtime()
        self.tasks: dict[str, Task] = {"t1": Task("t1", args.follower_min, args.follower_max, args.follower_min)}
        self.events: list[dict[str, Any]] = []
        self.next_id = 2
        self.restore_if_present()

    def restore_if_present(self) -> None:
        if not self.state_path.exists():
            return
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            objective = saved.get("objective", {})
            identity = (objective.get("platform"), objective.get("country"), objective.get("language"),
                        objective.get("follower_min"), objective.get("follower_max"), objective.get("recent"))
            expected = (self.args.platform, self.args.country, self.args.language,
                        self.args.follower_min, self.args.follower_max, self.args.recent)
            if saved.get("workflow") != STATE_VERSION or identity != expected:
                return
            keys = ("id", "low", "high", "scope_low", "status", "count", "parent_id", "reason",
                    "collection_page")
            tasks = {item["id"]: Task(**{key: item.get(key) for key in keys}) for item in saved.get("tasks", [])}
            if not tasks or any(task.scope_low is None for task in tasks.values()):
                return
            self.tasks, self.events = tasks, list(saved.get("events", []))
            self.next_id = int(saved.get("next_id", 2))
            self.events.append({"at": now(), "kind": "runtime", "value": "Resumed tail-agent state."})
        except (OSError, ValueError, TypeError, KeyError):
            return

    def active_task(self) -> Task | None:
        active = [task for task in self.tasks.values() if task.status in {"pending", "probed"}]
        if len(active) > 1:
            raise RuntimeError("State invariant failed: more than one active follower range.")
        return active[0] if active else None

    def completed(self) -> list[Task]:
        return sorted((task for task in self.tasks.values() if task.status == "collected"), key=lambda task: task.low)

    def probe_history(self) -> list[dict[str, int]]:
        return [{"min": task.low, "max": task.high, "count": task.count}
                for task in self.tasks.values() if task.count is not None]

    def context(self, feedback: str | None = None) -> dict[str, Any]:
        current = self.active_task()
        return {
            "original_user_request": getattr(self.args, "request", None),
            "platform": self.args.platform, "country": self.args.country, "language": self.args.language,
            "recent_days": self.args.recent, "hard_cap": self.args.cap,
            "target_range": [self.args.follower_min, self.args.follower_max],
            "remaining_range": [current.scope_low, current.high] if current else None,
            "current_probe": ({"min": current.low, "max": current.high, "count": current.count}
                              if current and current.status == "probed" else None),
            "probe_history": self.probe_history(),
            "completed_ranges": [{"min": task.low, "max": task.high, "count": task.count}
                                 for task in self.completed()],
            "feedback": feedback,
        }

    def save(self, status: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "workflow": STATE_VERSION, "updated_at": now(), "status": status,
            "objective": {"platform": self.args.platform, "country": self.args.country, "language": self.args.language,
                          "follower_min": self.args.follower_min, "follower_max": self.args.follower_max,
                          "recent": self.args.recent, "hard_cap": self.args.cap,
                          "completion_criterion": "full_contiguous_range_coverage"},
            "tasks": [task.public() for task in self.tasks.values()],
            "events": self.events, "next_id": self.next_id,
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def decide(self, feedback: str | None = None) -> dict[str, Any]:
        attempts = max(1, int(getattr(self.args, "decision_retries", 3)))
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                action = ask_agent_json(SYSTEM, self.context(feedback))
                self.events.append({"at": now(), "kind": "model_action", "value": action})
                self.save("running")
                return action
            except Exception as error:
                last_error = error
                if attempt == attempts:
                    break
                delay = min(10, attempt * 2)
                print(f"[DECISION-RETRY] {error}; retry {attempt}/{attempts - 1} in {delay}s", flush=True)
                time.sleep(delay)
        raise RuntimeError(f"decision request failed after {attempts} attempt(s): {last_error}") from last_error

    def query_with_retry(self, low: int, high: int, page: int, *, phase: str) -> Any:
        """Retry transient browser/API failures without abandoning this range."""
        attempts = max(1, int(getattr(self.args, "query_retries", 3)))
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.collector.query(
                    self.args.country, self.args.language, low, high, self.args.recent, page,
                )
            except Exception as error:
                if any(marker.lower() in str(error).lower() for marker in DAILY_QUOTA_MARKERS):
                    raise DailyQuotaExceeded(
                        "WotoHub 当日访问额度已用完；已保存断点，请在额度恢复后继续运行。"
                    ) from error
                last_error = error
                if attempt == attempts:
                    break
                delay = min(10, attempt * 2)
                print(f"[RETRY] {phase} {low}-{high}, page {page}: {error}; retry {attempt}/{attempts - 1} in {delay}s", flush=True)
                time.sleep(delay)
        raise RuntimeError(f"{phase} {low}-{high}, page {page} failed after {attempts} attempt(s): {last_error}") from last_error

    @staticmethod
    def extract_count(data: Any) -> int:
        if isinstance(data, dict) and "count" in data:
            return int(data["count"])
        if isinstance(data, tuple):
            for value in data:
                if isinstance(value, dict) and "count" in value:
                    return int(value["count"])
                if isinstance(value, int):
                    return value
        raise TypeError(f"Unsupported count response: {type(data).__name__}")

    @staticmethod
    def action_range(action: dict[str, Any]) -> tuple[int, int]:
        try:
            low, high = action["follower_min"], action["follower_max"]
        except KeyError as error:
            raise ValueError("probe and collect actions require follower_min and follower_max") from error
        if isinstance(low, bool) or isinstance(high, bool) or not isinstance(low, int) or not isinstance(high, int):
            raise ValueError("follower_min and follower_max must be integers")
        if low >= high:
            raise ValueError("follower_min must be strictly smaller than follower_max")
        return low, high

    def probe(self, action: dict[str, Any]) -> str:
        current = self.active_task()
        if current is None:
            raise ValueError("probe rejected: all ranges are already completed")
        low, high = self.action_range(action)
        if current.status == "pending":
            if (low, high) != (current.low, current.high):
                raise ValueError(f"first probe for remaining range must be {current.low}-{current.high}")
            task = current
            replacement = False
        else:
            if current.count is None or current.count < self.args.cap:
                raise ValueError("a safe probed range must be collected, not probed again")
            if high != current.high:
                raise ValueError(f"overloaded retry must keep follower_max={current.high}")
            if not current.low < low < high:
                raise ValueError("overloaded retry must strictly raise follower_min inside the current probe")
            task = Task(f"t{self.next_id}", low, high, current.scope_low, parent_id=current.id)
            replacement = True

        data = self.query_with_retry(task.low, task.high, 1, phase="probe")
        if replacement:
            current.status = "superseded"
            self.next_id += 1
            self.tasks[task.id] = task
        task.count, task.status, task.reason = self.extract_count(data), "probed", str(action.get("reason", ""))
        message = f"[PROBE] {self.args.platform}/{self.args.country}/{self.args.language} {task.low}-{task.high} -> {task.count}"
        self.events.append({"at": now(), "kind": "probe", "value": message})
        print(message, flush=True)
        return message

    def collect(self, action: dict[str, Any]) -> str:
        current = self.active_task()
        if current is None or current.status != "probed":
            raise ValueError("collect requires exactly one probed active range")
        low, high = self.action_range(action)
        if (low, high) != (current.low, current.high):
            raise ValueError("collect must use the identical safe probed range")
        if current.count is None or current.count >= self.args.cap:
            raise ValueError(f"collect requires a verified count below {self.args.cap}")
        print(f"[ACCEPT] {current.low}-{current.high} -> {current.count}, start collection", flush=True)
        if self.args.execute:
            self.collect_current_language(current)
        current.status, current.reason = "collected", str(action.get("reason", ""))
        if current.scope_low < current.low:
            remaining = Task(f"t{self.next_id}", current.scope_low, current.low - 1, current.scope_low, parent_id=current.id)
            self.next_id += 1
            self.tasks[remaining.id] = remaining
            print(f"[REMAINING] {remaining.low}-{remaining.high}", flush=True)
        message = f"[COLLECTED] {current.low}-{current.high} -> {current.count}"
        self.events.append({"at": now(), "kind": "collect", "value": message})
        print(message, flush=True)
        return message

    def validate_coverage(self) -> None:
        expected = self.args.follower_min
        for task in self.completed():
            if task.low != expected or task.high < task.low or task.count is None or task.count >= self.args.cap:
                raise ValueError("completed ranges do not form a safe, contiguous target coverage")
            expected = task.high + 1
        if expected != self.args.follower_max + 1:
            raise ValueError(f"coverage incomplete: next uncovered follower value is {expected}")

    def done(self) -> tuple[bool, str]:
        if self.active_task() is not None:
            raise ValueError("done rejected: a follower range remains active")
        self.validate_coverage()
        final_ranges = ", ".join(f"{task.low}-{task.high} ({task.count})" for task in self.completed())
        message = f"[DONE] target {self.args.follower_min}-{self.args.follower_max} fully covered; {final_ranges}"
        self.events.append({"at": now(), "kind": "done", "value": message})
        print(message, flush=True)
        return True, message

    def collect_current_language(self, task: Task) -> None:
        # Per-task partial data is kept beside the resumable state, never in the
        # output folder.  The only user-facing exports are platform total tables.
        checkpoint_base = self.state_path.with_suffix(f".{task.id}.handles.csv")
        partial_path = checkpoint_base.with_suffix(".partial.csv")
        # The partial CSV is the durable page checkpoint.  Keeping handles out
        # of the JSON state prevents the desktop response layer from trying to
        # render multi-megabyte state objects after each page.
        handles: list[str] = []
        if partial_path.exists():
            with open(partial_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                next(reader, None)
                handles = [row[0].strip() for row in reader if row and row[0].strip()]
        if handles and task.collection_page == 1:
            task.collection_page = len(handles) // 500 + 1
        page = task.collection_page
        while True:
            response, page_handles = self.query_with_retry(task.low, task.high, page, phase="collection")
            handles.extend(str(handle) for handle in page_handles if handle)
            partial_path = self.runtime["atomic_csv"](checkpoint_base, handles, partial=True)
            print(f"[OUTPUT] {partial_path} ({len(handles)} handles, page {page})", flush=True)
            if not response.get("hasNextPage"):
                break
            page += 1
            task.collection_page = page
            self.events.append({"at": now(), "kind": "checkpoint",
                                "value": f"collection checkpoint: {task.id}, next page {page}, handles {len(handles)}"})
            self.save("running")
            page_delay = self.runtime.get("page_delay_seconds")
            if page_delay:
                delay = random.uniform(*page_delay)
                logger = self.runtime.get("log_activity")
                if logger:
                    logger("page_wait", seconds=round(delay, 2), platform=self.args.platform,
                           country=self.args.country, language=self.args.language, page=page)
                print(f"[PACE] page {page} complete; waiting {delay:.1f}s before next page", flush=True)
                time.sleep(delay)
            else:
                time.sleep(self.runtime["PAGE_DELAY"] / 1000)
        if len(handles) != task.count:
            raise RuntimeError(f"collection row mismatch: expected {task.count}, received {len(handles)}")
        total_path, inserted = append_platform_rows(
            self.args.platform, self.args.country, self.args.language,
            f"{task.low}-{task.high}", handles,
        )
        partial_path.unlink(missing_ok=True)
        task.collection_page = page
        print(f"[OUTPUT] {total_path} (+{inserted} rows; {len(handles)} handles)", flush=True)

    def checkpoint_failure(self, error: BaseException) -> None:
        message = f"[INTERRUPTED] {type(error).__name__}: {error}"
        self.events.append({"at": now(), "kind": "interruption", "value": message})
        self.save("interrupted")
        print(message, flush=True)
        print(f"[RESUME] Progress was saved to {self.state_path}. Re-run the same command to continue.", flush=True)

    def apply(self, action: dict[str, Any]) -> tuple[bool, str]:
        kind = action.get("action")
        if kind in {"probe", "collect"}:
            low, high = self.action_range(action)
            print(f"[DECISION] {kind}: {low}-{high}", flush=True)
        if kind == "probe":
            return False, self.probe(action)
        if kind == "collect":
            return False, self.collect(action)
        if kind == "done":
            return self.done()
        raise ValueError("action must be probe, collect, or done")

    def run(self) -> None:
        feedback = None
        for _ in range(self.args.max_steps):
            # A resumed state may already have every range collected.  Finish
            # locally instead of asking the model for a redundant collect.
            if self.active_task() is None:
                finished, _ = self.done()
                self.save("complete" if finished else "running")
                return
            action = self.decide(feedback)
            try:
                finished, feedback = self.apply(action)
                self.save("complete" if finished else "running")
                if finished:
                    return
            except ValueError as error:
                feedback = f"Runtime rejected the prior action: {error}"
                self.events.append({"at": now(), "kind": "rejection", "value": feedback})
                self.save("running")
                print(feedback, flush=True)
        raise RuntimeError(f"model loop reached max_steps={self.args.max_steps}")


def main() -> None:
    p = argparse.ArgumentParser(description="Run an LLM-directed WotoHub follower-range collection loop.")
    p.add_argument("--platform", required=True, choices=("youtube", "ins", "instagram", "tiktok"))
    p.add_argument("--country", required=True)
    p.add_argument("--language", required=True)
    p.add_argument("--follower-min", type=int, required=True)
    p.add_argument("--follower-max", type=int, default=1_000_000_000)
    p.add_argument("--recent", type=int, default=30, choices=(0, 30, 60, 90))
    p.add_argument("--cap", type=int, default=10_000)
    p.add_argument("--max-steps", type=int, default=2_000)
    p.add_argument("--query-retries", type=int, default=3)
    p.add_argument("--decision-retries", type=int, default=3)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--state-file", type=Path)
    p.add_argument("--request", default="")
    args = p.parse_args()
    if args.follower_min < 0 or args.follower_max <= args.follower_min:
        p.error("follower-max must be greater than follower-min")
    args.platform = "ins" if args.platform == "instagram" else args.platform
    state = args.state_file or HERE / "model_runs" / f"{args.platform}_{args.country}_{args.language}_{args.follower_min}-{args.follower_max}.json"
    runtime = load_runtime()
    collector = runtime["Collector"](runtime["PLATFORM_ALIASES"][args.platform])
    loop = ModelLoop(args, collector, state, runtime)
    try:
        loop.run()
    except BaseException as error:
        loop.checkpoint_failure(error)
        raise
    finally:
        collector.close()


if __name__ == "__main__":
    main()
