"""Expand an approved plan and run its tasks through the model decision loop."""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from argparse import Namespace
from pathlib import Path

from model_driven_search_agent import DailyQuotaExceeded, ModelLoop, load_runtime


HERE = Path(__file__).resolve().parent

def unique_output_count(platform: str, country: str) -> int:
    """Count unique handles for one country in its platform-level total CSV."""
    seen = set()
    path = HERE / "output" / f"{platform}_总表.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("国家") == country and row.get("handle", "").strip():
                    seen.add(row["handle"].strip().lstrip("@"))
    except OSError:
        pass
    return len(seen)


def task_is_complete(state: Path) -> bool:
    """Return whether a task state has reached a verified terminal result.

    Keep this lightweight and tolerant of legacy state files so resuming a
    batch never reopens task 1 merely to discover it was already finished.
    """
    try:
        return '"status": "complete"' in state.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def language_codes() -> list[str]:
    data = json.loads((HERE / "discovered_options.json").read_text(encoding="utf-8"))
    return [item["value"] for item in data["languages"]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--plan", required=True, type=Path)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--max-steps", type=int, default=80)
    p.add_argument("--task-retries", type=int, default=3)
    args = p.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    runtime, languages = load_runtime(), language_codes()
    run_root = HERE / "model_runs" / args.plan.stem
    failures: list[str] = []
    country_totals: dict[tuple[str, str], int] = {}
    collector = None
    collector_platform = None
    try:
        for index, item in enumerate(plan["tasks"], 1):
            selected = [item["language"]] if item["language"] != "auto" else runtime["selected_languages"](item["country"], languages)
            for language in selected:
                job = Namespace(
                    platform=item["platform"], country=item["country"], language=language,
                    follower_min=int(item["follower_min"]), follower_max=int(item["follower_max"]),
                    recent=int(item["recent"]), cap=10_000, max_steps=args.max_steps,
                    query_retries=3, decision_retries=3,
                    execute=args.execute, request=plan["request"],
                )
                state = run_root / f"{index:04d}_{job.platform}_{job.country}_{language}.json"
                if task_is_complete(state):
                    print(f"SKIP COMPLETE {index}/{len(plan['tasks'])}: {job.platform} {job.country} {language}", flush=True)
                    continue
                if collector is None or collector_platform != job.platform:
                    if collector is not None:
                        try: collector.close()
                        except Exception as error: print(f"[CLOSE-WARNING] {error}", flush=True)
                    collector = runtime["Collector"](runtime["PLATFORM_ALIASES"][job.platform])
                    collector_platform = job.platform
                    print(f"[BROWSER] Connected once for {job.platform}; reusing it for subsequent countries.", flush=True)
                print(f"START {index}/{len(plan['tasks'])}: {job.platform} {job.country} {language}", flush=True)
                for attempt in range(1, max(1, args.task_retries) + 1):
                    loop = ModelLoop(job, collector, state, runtime)
                    try:
                        loop.run()
                        total = sum(int(task.count or 0) for task in loop.completed())
                        country_totals[(job.platform, job.country)] = country_totals.get((job.platform, job.country), 0) + total
                        print(f"COUNTRY RESULT {job.platform}/{job.country}/{job.language}: {total} 条；当前国家累计 {country_totals[(job.platform, job.country)]} 条", flush=True)
                        cooldown = runtime.get("task_cooldown_seconds")
                        if cooldown:
                            delay = random.uniform(*cooldown)
                            runtime.get("log_activity", lambda *_args, **_kwargs: None)(
                                "task_cooldown", seconds=round(delay, 2), platform=job.platform,
                                country=job.country, language=job.language,
                            )
                            print(f"[PACE] task complete; waiting {delay / 60:.1f} min before next task", flush=True)
                            time.sleep(delay)
                        break
                    except DailyQuotaExceeded as error:
                        loop.checkpoint_failure(error)
                        print(f"[DAILY-QUOTA] {error}", flush=True)
                        raise SystemExit(2)
                    except Exception as error:
                        loop.checkpoint_failure(error)
                        if attempt == max(1, args.task_retries):
                            message = f"FAILED {job.platform} {job.country} {language}: {error}"
                            failures.append(message)
                            print(message, flush=True)
                            print(f"[RESUME] Re-run this batch; saved task state: {state}", flush=True)
                        else:
                            print(f"[TASK-RETRY] {job.platform}/{job.country}/{language}: {error}; retry {attempt}/{args.task_retries - 1}", flush=True)
                            try: collector.close()
                            except Exception as close_error: print(f"[CLOSE-WARNING] {close_error}", flush=True)
                            collector = runtime["Collector"](runtime["PLATFORM_ALIASES"][job.platform])
                            collector_platform = job.platform
                            print(f"[BROWSER] Reconnected after task error: {job.platform}", flush=True)
                            time.sleep(min(10, attempt * 2))
    finally:
        if collector is not None:
            try: collector.close()
            except Exception as error: print(f"[CLOSE-WARNING] {error}", flush=True)
    if failures:
        print(f"BATCH COMPLETE WITH {len(failures)} FAILED TASK(S)", flush=True)
        raise SystemExit(1)
    for (platform, country), total in sorted(country_totals.items()):
        unique = unique_output_count(platform, country)
        print(f"国家汇总 {country}（{platform}）：共 {unique} 条唯一结果（任务累计 {total} 条）", flush=True)
    print("BATCH COMPLETE", flush=True)


if __name__ == "__main__":
    main()
