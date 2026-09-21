"""Load the compiled WotoHub collector runtime used by the agent workflow."""
from __future__ import annotations

import runpy
import sys
import queue
import threading
import json
import random
import time
from datetime import date
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
RUNTIME_PYC = HERE / "__pycache__" / "vue_full_collector.cpython-313.pyc"
CONFIG_PATH = HERE / "config.yaml"
ACTIVITY_LOG = HERE / "model_runs" / "activity.jsonl"
DAILY_BUDGET_PATH = HERE / "model_runs" / "daily_query_budget.json"


def _settings() -> dict[str, Any]:
    try:
        import yaml
        return (yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("low_frequency", {})
    except (OSError, ValueError, TypeError, ImportError):
        return {}


def _range_seconds(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    try:
        low, high = float(value[0]), float(value[1])
        return min(low, high), max(low, high)
    except (TypeError, ValueError, IndexError):
        return default


def _log_activity(kind: str, **values: Any) -> None:
    ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **values}
    with ACTIVITY_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


class DailyRequestBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = max(0, maximum)
        self.lock = threading.Lock()

    def consume(self, **context: Any) -> int:
        if not self.maximum:
            return 0
        with self.lock:
            today = date.today().isoformat()
            try:
                saved = json.loads(DAILY_BUDGET_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                saved = {}
            count = int(saved.get("count", 0)) if saved.get("date") == today else 0
            if count >= self.maximum:
                _log_activity("daily_budget_reached", count=count, maximum=self.maximum, **context)
                raise RuntimeError(f"本地每日请求保护阈值已达到：{count}/{self.maximum} 页；已保存断点，请明天继续运行。")
            count += 1
            DAILY_BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
            DAILY_BUDGET_PATH.write_text(json.dumps({"date": today, "count": count, "maximum": self.maximum}, ensure_ascii=False, indent=2), encoding="utf-8")
            _log_activity("query", count=count, maximum=self.maximum, **context)
            return count


def _response_collector(base: type[Any]) -> type[Any]:
    """Use the search API response as the export source.

    The page's Vue state is useful for driving its native search method, but it
    is not a reliable export contract: transient frontend rendering failures
    can throw JavaScript errors after the HTTP response has already arrived.
    """
    class ResponseCollector(base):
        def __init__(self, platform: str) -> None:
            super().__init__(platform)
            settings = _settings()
            self._low_frequency = bool(settings.get("enabled", True))
            self._query_interval = _range_seconds(settings.get("query_interval_seconds"), (5, 8))
            self._last_query_at = 0.0
            self._budget = DailyRequestBudget(int(settings.get("daily_max_query_pages", 300)))

        @staticmethod
        def _has_search_host(page: Any) -> bool:
            try:
                return bool(page.evaluate("""() => [...document.querySelectorAll('*')].some(el => {
                    const c = el.__vue__ || el.__vueParentComponent;
                    const x = c?.proxy || c?.ctx || c;
                    return x?.queryParams && typeof x.getBloggerList === 'function';
                })"""))
            except Exception:
                return False

        def _search_page(self) -> Any:
            for context in self.browser.contexts:
                for page in context.pages:
                    if "wotohub.com" in page.url and self._has_search_host(page):
                        self.page = page
                        return page
            self.page.goto("https://www.wotohub.com/workbenchSearch", wait_until="domcontentloaded")
            for _ in range(4):
                self.page.wait_for_timeout(1_000)
                if self._has_search_host(self.page):
                    return self.page
            raise RuntimeError("WotoHub search page did not initialize; check the persistent browser login.")

        def close(self) -> None:
            # Playwright's sync transport can occasionally try to run its own
            # event loop while Anaconda already owns one.  The browser is a
            # shared CDP session, so a failed detach must never turn a completed
            # collection into a failed batch task.
            try:
                super().close()
            except RuntimeError as error:
                if "event loop is already running" not in str(error):
                    raise
                print(f"[CLOSE-WARNING] Browser detach deferred: {error}", flush=True)

        def query(self, country: str, lang: str | None, low: int, high: int,
                  recent_days: int, page_no: int):
            context = {"platform": self.platform, "country": country, "language": lang or "", "low": low, "high": high, "page": page_no}
            self._budget.consume(**context)
            if self._low_frequency:
                target = random.uniform(*self._query_interval)
                remaining = target - (time.monotonic() - self._last_query_at)
                if remaining > 0:
                    _log_activity("query_wait", seconds=round(remaining, 2), **context)
                    time.sleep(remaining)
            page = self._search_page()
            recent = "RECENT_ALL" if not recent_days else f"RECENT_{recent_days}D"

            def is_target_response(response: Any) -> bool:
                if not response.url.endswith("/dataService/home/search") or response.request.method != "POST":
                    return False
                try:
                    body = response.request.post_data_json or {}
                    regions = body.get("regionList") or []
                    return (
                        any(country in (item.get("country") or []) for item in regions)
                        and body.get("platform") == self.platform
                        and body.get("blogLangs") == ([lang] if lang else [])
                        and body.get("minFansNum") == low
                        and body.get("maxFansNum") == high
                        and body.get("searchRecent") == recent
                        and body.get("pageNum") == page_no
                        and body.get("pageSize") == 500
                    )
                except Exception:
                    return False

            with page.expect_response(is_target_response, timeout=30_000) as event:
                page.evaluate(
                    """async a => {
                      let search, seen = new Set();
                      for (const el of document.querySelectorAll('*')) {
                        const component = el.__vue__ || el.__vueParentComponent;
                        const candidate = component?.proxy || component?.ctx || component;
                        if (candidate && !seen.has(candidate) && candidate.queryParams && typeof candidate.getBloggerList === 'function') { search = candidate; break; }
                        if (candidate) seen.add(candidate);
                      }
                      if (!search) throw new Error('WotoHub Vue search parent unavailable');
                      const group = search.countryObj.optionsArr.find(g => g.optionsItem.some(o => o.id === a.country));
                      if (!group) throw new Error('country group unavailable: ' + a.country);
                      Object.assign(search.queryParams, {
                        platform: a.platform, regionList: [{id: group.id, country: [a.country]}],
                        blogLangs: a.lang ? [a.lang] : [], minFansNum: a.low, maxFansNum: a.high,
                        searchRecent: a.recent, pageNum: a.page, pageSize: 500, searchFilterList: []
                      });
                      await search.getBloggerList();
                    }""",
                    {"platform": self.platform, "country": country, "lang": lang, "low": low,
                     "high": high, "recent": recent, "page": page_no},
                )

            self._last_query_at = time.monotonic()
            raw = event.value.json()
            data = raw.get("data") if isinstance(raw, dict) else None
            if raw.get("code") != "0" or not isinstance(data, dict):
                raise RuntimeError(raw.get("message", "search response missing data"))
            rows = data.get("bloggerList") or []
            if not isinstance(rows, list):
                raise RuntimeError(f"search response bloggerList must be a list, got {type(rows).__name__}")
            handles = [str(row.get("username") or "").lstrip("@").strip()
                       for row in rows if isinstance(row, dict) and row.get("username")]
            return data, handles

    return ResponseCollector


def _threaded_collector(base: type[Any]) -> type[Any]:
    """Run Playwright Sync API on a dedicated thread.

    Some Anaconda launch paths keep an asyncio loop alive on the application
    thread.  Playwright rejects its Sync API there, but it works normally on a
    fresh worker thread.  Calls remain synchronous to the collection loop.
    """
    class ThreadedCollector:
        def __init__(self, platform: str) -> None:
            self._requests: queue.Queue[tuple[str, tuple[Any, ...], queue.Queue[Any]]] = queue.Queue()
            self._ready: queue.Queue[Any] = queue.Queue(maxsize=1)
            self._thread = threading.Thread(target=self._serve, args=(platform,), daemon=True, name="wotohub-playwright")
            self._thread.start()
            result = self._ready.get()
            if isinstance(result, BaseException):
                raise result

        def _serve(self, platform: str) -> None:
            collector: Any = None
            try:
                collector = base(platform)
                self._ready.put(True)
                while True:
                    method, args, reply = self._requests.get()
                    if method == "close":
                        try:
                            collector.close()
                            reply.put(None)
                        except BaseException as error:
                            reply.put(error)
                        return
                    try:
                        reply.put(getattr(collector, method)(*args))
                    except BaseException as error:
                        if method == "query" and "Vue search parent unavailable" in str(error):
                            try:
                                collector.page.goto("https://www.wotohub.com/workbenchSearch", wait_until="domcontentloaded")
                                collector.page.wait_for_timeout(3_000)
                                reply.put(getattr(collector, method)(*args))
                            except BaseException as retry_error:
                                reply.put(retry_error)
                        else:
                            reply.put(error)
            except BaseException as error:
                self._ready.put(error)

        def _call(self, method: str, *args: Any) -> Any:
            reply: queue.Queue[Any] = queue.Queue(maxsize=1)
            self._requests.put((method, args, reply))
            result = reply.get()
            if isinstance(result, BaseException):
                raise result
            return result

        def query(self, country: str, lang: str | None, low: int, high: int,
                  recent_days: int, page_no: int) -> Any:
            return self._call("query", country, lang, low, high, recent_days, page_no)

        def close(self) -> None:
            if self._thread.is_alive():
                try:
                    self._call("close")
                finally:
                    self._thread.join(timeout=10)

    return ThreadedCollector


def load_runtime() -> dict[str, Any]:
    if not RUNTIME_PYC.exists():
        raise RuntimeError(f"Collector runtime is missing: {RUNTIME_PYC}")
    sys.path.insert(0, str(HERE))
    runtime = runpy.run_path(str(RUNTIME_PYC), run_name="wotohub_agent_runtime")
    runtime["Collector"] = _threaded_collector(_response_collector(runtime["Collector"]))
    settings = _settings()
    runtime["page_delay_seconds"] = _range_seconds(settings.get("page_interval_seconds"), (8, 15))
    runtime["task_cooldown_seconds"] = _range_seconds(settings.get("task_cooldown_seconds"), (300, 600))
    runtime["log_activity"] = _log_activity
    return runtime
