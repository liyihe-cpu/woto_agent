from __future__ import annotations

import queue
import threading
import tkinter as tk
import ctypes
import os
from pathlib import Path
from tkinter import filedialog, scrolledtext

from agent_core import Plan, make_plan, start_collector

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    ROOT_BASE = TkinterDnD.Tk
except ImportError:  # Lets the UI still open before dependencies are installed.
    DND_FILES = None
    ROOT_BASE = tk.Tk


class AgentApp(ROOT_BASE):
    """A compact, single-conversation desktop interface for the collector."""

    BG, PANEL, INPUT = "#0F172A", "#172033", "#202C42"
    TEXT, MUTED, ACCENT = "#E5E7EB", "#94A3B8", "#5EEAD4"

    def __init__(self):
        super().__init__()
        self.title("WotoHub · Handle 采集")
        self.geometry("760x680")
        self.minsize(520, 420)
        self.configure(bg=self.BG)
        self.files: list[Path] = []
        self.conversation: list[tuple[str, str]] = []
        self.plan: Plan | None = None
        self.process = None
        self.paused = False
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()

        self.shell = tk.Frame(self, bg=self.BG)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        tk.Label(self.shell, text="需要爬哪种类型的 handle？", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 18, "bold"), anchor="w").pack(fill=tk.X)
        tk.Label(self.shell, text="告诉我平台、国家或地区、粉丝范围与发布时间，我会生成采集计划。",
                 bg=self.BG, fg=self.MUTED, font=("Microsoft YaHei UI", 10), anchor="w").pack(fill=tk.X, pady=(6, 16))

        self.chat = scrolledtext.ScrolledText(self.shell, wrap=tk.WORD, state="disabled", relief=tk.FLAT,
            borderwidth=0, bg=self.PANEL, fg=self.TEXT, insertbackground=self.TEXT,
            font=("Microsoft YaHei UI", 10), padx=18, pady=14)
        self.chat.pack(fill=tk.BOTH, expand=True)
        self.chat.tag_configure("agent", foreground=self.ACCENT, font=("Microsoft YaHei UI", 10, "bold"))
        self.chat.tag_configure("user", foreground="#93C5FD", font=("Microsoft YaHei UI", 10, "bold"))
        self.chat.tag_configure("body", foreground=self.TEXT, font=("Microsoft YaHei UI", 10))

        composer = tk.Frame(self.shell, bg=self.INPUT, padx=12, pady=10, height=122)
        composer.pack(fill=tk.X, pady=(14, 0))
        composer.pack_propagate(False)
        self.entry = tk.Text(composer, height=3, wrap=tk.WORD, relief=tk.FLAT, borderwidth=0,
            bg=self.INPUT, fg=self.TEXT, insertbackground=self.TEXT, font=("Microsoft YaHei UI", 10))
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        self.entry.bind("<Control-Return>", lambda _event: self.submit())
        self.entry.bind("<Control-v>", self.paste_files)
        self.entry.bind("<Control-V>", self.paste_files)
        buttons = tk.Frame(composer, bg=self.INPUT)
        buttons.pack(side=tk.RIGHT, fill=tk.Y)
        self.attach_button = self.button(buttons, "附件", self.add_files, secondary=True)
        self.attach_button.pack(fill=tk.X, pady=(0, 6))
        self.confirm_button = self.button(buttons, "确认", self.submit)
        self.confirm_button.pack(fill=tk.X)
        self.pause_button = self.button(buttons, "暂停", self.toggle_pause, secondary=True)
        self.pause_button.pack(fill=tk.X, pady=(6, 0))
        self.pause_button.config(state=tk.DISABLED)

        if DND_FILES:
            for widget in (self.chat, self.entry):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.drop_files)

        self.write("Agent", "你好，想采集什么类型的 handle？\n例如：巴西和东南亚的 YouTube，粉丝 100 万到 1100 万，近 30 天发布。")
        self.after(150, self.drain_events)

    def button(self, parent: tk.Misc, text: str, command, secondary: bool = False) -> tk.Button:
        return tk.Button(parent, text=text, command=command, cursor="hand2", relief=tk.FLAT,
            bg="#334155" if secondary else self.ACCENT, fg=self.TEXT if secondary else "#082F49",
            activebackground="#475569" if secondary else "#99F6E4", activeforeground=self.TEXT if secondary else "#082F49",
            font=("Microsoft YaHei UI", 10, "bold"), padx=14, pady=7)

    def write(self, role: str, text: str):
        tag = "agent" if role in ("Agent", "日志") else "user"
        self.chat.configure(state="normal")
        self.chat.insert(tk.END, f"{role}\n", tag)
        self.chat.insert(tk.END, f"{text}\n\n", "body")
        self.chat.configure(state="disabled")
        self.chat.see(tk.END)

    def add_files(self):
        paths = filedialog.askopenfilenames(title="添加附件", filetypes=[("支持的文件", "*.png *.jpg *.jpeg *.webp *.txt *.md *.csv *.xlsx *.xls *.json *.yaml *.yml"), ("所有文件", "*.*")])
        self.add_attachment_paths(paths)

    def add_attachment_paths(self, paths):
        """Add unique files from a picker, Explorer drop, or Windows clipboard."""
        added = [Path(item) for item in paths if Path(item).is_file()]
        if not added:
            return
        known = {path.resolve() for path in self.files}
        self.files.extend(path for path in added if path.resolve() not in known)
        self.attach_button.config(text=f"附件 · {len(self.files)}")
        self.write("Agent", "已添加：" + "、".join(path.name for path in added))

    def drop_files(self, event):
        self.add_attachment_paths(self.tk.splitlist(event.data))
        return "break"

    @staticmethod
    def clipboard_file_paths() -> list[str]:
        """Read files copied in Windows Explorer (CF_HDROP) without pywin32."""
        CF_HDROP = 15
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        if not user32.OpenClipboard(None):
            return []
        try:
            if not user32.IsClipboardFormatAvailable(CF_HDROP):
                return []
            handle = user32.GetClipboardData(CF_HDROP)
            if not handle:
                return []
            count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
            return [
                ctypes.create_unicode_buffer(shell32.DragQueryFileW(handle, index, None, 0) + 1)
                for index in range(count)
            ]
        finally:
            user32.CloseClipboard()

    def paste_files(self, _event):
        paths = self.clipboard_file_paths()
        if not paths:
            self.entry.event_generate("<<Paste>>")
            return "break"
        self.add_attachment_paths([buffer.value for buffer in paths])
        return "break"

    def toggle_pause(self):
        """Suspend or resume the collector process without losing batch state."""
        if not self.process or self.process.poll() is not None:
            return
        if os.name != "nt":
            self.write("Agent", "当前系统不支持进程暂停。")
            return
        handle = ctypes.windll.kernel32.OpenProcess(0x0800, False, self.process.pid)
        if not handle:
            self.write("Agent", "暂停操作失败，请检查采集进程状态。")
            return
        try:
            action = ctypes.windll.ntdll.NtResumeProcess if self.paused else ctypes.windll.ntdll.NtSuspendProcess
            action.restype = ctypes.c_long
            if action(handle) != 0:
                self.write("Agent", "暂停操作失败，请检查采集进程状态。")
                return
            self.paused = not self.paused
            self.pause_button.config(text="继续" if self.paused else "暂停")
            self.write("Agent", "采集已暂停。" if self.paused else "采集已继续。")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    def submit(self):
        text = self.entry.get("1.0", tk.END).strip()
        if not text and not self.files:
            self.entry.focus_set()
            return
        self.plan = None
        self.confirm_button.config(state=tk.DISABLED, text="生成中…")
        self.write("你", text or "请根据附件生成采集计划")
        self.write("Agent", "正在生成检索方案…")
        self.conversation.append(("user", text or "请根据附件生成采集计划"))
        self.entry.delete("1.0", tk.END)
        threading.Thread(target=self.make_plan_worker, args=(text,), daemon=True).start()

    def make_plan_worker(self, text: str):
        try:
            self.plan = make_plan(text, self.files, self.conversation)
            self.events.put(("plan", self.plan.summary() + f"\n解析方式：{self.plan.source}"))
        except Exception as error:
            self.events.put(("error", str(error)))

    def run_plan(self):
        if not self.plan:
            return
        if self.process and self.process.poll() is None:
            self.write("Agent", "已有采集任务正在运行。")
            return
        try:
            self.confirm_button.config(state=tk.DISABLED, text="运行中…")
            self.write("Agent", "检索方案正在运行…")
            self.process = start_collector(self.plan)
            self.paused = False
            self.pause_button.config(state=tk.NORMAL, text="暂停")
            self.write("Agent", "检索方案已启动，执行日志会显示在这里。")
            threading.Thread(target=self.log_worker, daemon=True).start()
        except Exception as error:
            self.write("Agent", f"启动失败：{error}")
            self.confirm_button.config(state=tk.NORMAL, text="确认")

    def log_worker(self):
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.events.put(("log", line.rstrip()))
        self.events.put(("done", f"采集进程结束，退出码：{self.process.returncode}"))

    def drain_events(self):
        while not self.events.empty():
            kind, value = self.events.get()
            self.write("日志" if kind == "log" else "Agent", value)
            if kind == "plan":
                self.confirm_button.config(state=tk.NORMAL, text="确认")
                self.run_plan()
            elif kind == "error":
                self.confirm_button.config(state=tk.NORMAL, text="确认")
            elif kind == "done":
                self.paused = False
                self.pause_button.config(state=tk.DISABLED, text="暂停")
                self.confirm_button.config(state=tk.NORMAL, text="确认")
        self.after(150, self.drain_events)


if __name__ == "__main__":
    AgentApp().mainloop()
