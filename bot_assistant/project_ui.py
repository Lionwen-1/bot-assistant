"""AstrBot project operations inside the desktop control desk."""

from __future__ import annotations

import threading
import queue
import tkinter as tk
from tkinter import messagebox, ttk

from .remote import Remote, RemoteError, bundled_thinking_plugin
from .local import Local
from .native import NativeLocal

BG = "#101815"
SURFACE = "#1C2923"
SURFACE_2 = "#273B31"
TEXT = "#FAF7ED"
MUTED = "#ADC0B3"
AMBER = "#F1B45B"
JADE = "#88D5BA"
RED = "#EF9186"
FONT = "Microsoft YaHei UI"


class ProjectWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk, remote: Remote, open_dashboard,
                 icon_path=None, configure_native=None):
        super().__init__(parent, bg=BG)
        self.remote = remote
        self.project_path = remote.profile.project_path
        self.is_local = isinstance(remote, (Local, NativeLocal))
        self.is_native = isinstance(remote, NativeLocal)
        self.configure_native = configure_native
        self.open_dashboard = open_dashboard
        self.busy = False
        self.events: queue.Queue[tuple] = queue.Queue()
        self.services: list[str] = []
        self.title("bot助手 · AstrBot 项目运维")
        if icon_path:
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass
        self.geometry("960x750")
        self.minsize(820, 600)
        self.transient(parent)
        self._style()
        self._build()
        self.after(100, self._pump)
        self.refresh()

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Bot.TNotebook", background=BG, borderwidth=0)
        style.configure("Bot.TNotebook.Tab", background=SURFACE, foreground=MUTED,
                        padding=(18, 10), font=(FONT, 10))
        style.map("Bot.TNotebook.Tab", background=[("selected", SURFACE_2)],
                  foreground=[("selected", TEXT)])
        style.configure("Bot.Treeview", background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, rowheight=30, borderwidth=0, font=(FONT, 10))
        style.configure("Bot.Treeview.Heading", background=SURFACE_2,
                        foreground=TEXT, font=(FONT, 10, "bold"))
        style.map("Bot.Treeview", background=[("selected", "#39594B")])
        style.configure("Bot.TCombobox", fieldbackground=SURFACE,
                        background=SURFACE_2, foreground=TEXT)

    def _button(self, parent, label, command, accent=False):
        return tk.Button(parent, text=label, command=command, bd=0,
                         bg=AMBER if accent else SURFACE_2,
                         fg=BG if accent else TEXT, padx=14, pady=8,
                         activebackground="#F7C983" if accent else "#3A5143",
                         font=(FONT, 10, "bold" if accent else "normal"),
                         cursor="hand2")

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=24, pady=(18, 10))
        tk.Label(top, text="AstrBot 项目运维", bg=BG, fg=TEXT,
                 font=(FONT, 19, "bold")).pack(side="left")
        self._button(top, "打开 AstrBot 控制台 ↗", self.open_dashboard, True).pack(side="right")
        self._button(top, "↻ 刷新", self.refresh).pack(side="right", padx=(0, 9))
        tk.Label(self, text=self.project_path, bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=25)
        self.summary = tk.Label(self, text="正在读取项目…", bg=BG, fg=JADE,
                                font=(FONT, 11, "bold"))
        self.summary.pack(anchor="w", padx=25, pady=(12, 12))

        self.notebook = ttk.Notebook(self, style="Bot.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        self.service_tab = tk.Frame(self.notebook, bg=BG)
        self.log_tab = tk.Frame(self.notebook, bg=BG)
        self.backup_tab = tk.Frame(self.notebook, bg=BG)
        self.config_tab = tk.Frame(self.notebook, bg=BG)
        self.plugin_tab = tk.Frame(self.notebook, bg=BG)
        self.bot_tab = tk.Frame(self.notebook, bg=BG)
        for tab, label in ((self.service_tab, "服务"), (self.log_tab, "日志"),
                           (self.backup_tab, "备份"), (self.config_tab, "配置"),
                           (self.plugin_tab, "插件"), (self.bot_tab, "Bot 设置")):
            self.notebook.add(tab, text=label)
        self._build_services()
        self._build_logs()
        self._build_backups()
        self._build_configs()
        self._build_plugins()
        self._build_bot_settings()
        self.note = tk.Label(self, text=("本机免 Docker 项目；管理面板仅监听本机。" if self.is_native
                                         else "本机 Docker 项目；管理面板仅监听本机。" if self.is_local
                                         else "通过 SSH 读取；管理端口仅在本机建立临时隧道。"),
                             bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.note.pack(fill="x", padx=25, pady=(0, 12))

    def select_bot_settings(self):
        self.notebook.select(self.bot_tab)

    def _tree(self, parent, columns, headings, widths):
        tree = ttk.Treeview(parent, columns=columns, show="headings",
                            style="Bot.Treeview", selectmode="browse")
        for name, label, width in zip(columns, headings, widths):
            tree.heading(name, text=label)
            tree.column(name, width=width, anchor="w", stretch=True)
        tree.pack(fill="both", expand=True, padx=8, pady=(8, 12))
        return tree

    def _build_services(self):
        tk.Label(self.service_tab, text="本机进程" if self.is_native else "Compose 服务", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w", padx=10, pady=(14, 0))
        self.service_tree = self._tree(self.service_tab,
                                       ("name", "state", "status", "health"),
                                       ("服务", "状态", "详情", "健康检查"),
                                       (170, 110, 430, 130))
        bar = tk.Frame(self.service_tab, bg=BG)
        bar.pack(fill="x", padx=10, pady=(0, 12))
        self._button(bar, "查看所选日志", self.selected_logs).pack(side="left")
        self._button(bar, "重启所选服务", self.restart_selected).pack(side="right")
        tk.Label(self.service_tab, text="重启 NapCat 可能需要重新扫码；操作只作用于所选服务。",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=10)

    def _build_logs(self):
        bar = tk.Frame(self.log_tab, bg=BG)
        bar.pack(fill="x", padx=10, pady=(12, 8))
        tk.Label(bar, text="服务日志", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(side="left")
        self.log_service = tk.StringVar()
        self.log_choice = ttk.Combobox(bar, textvariable=self.log_service,
                                        state="readonly", width=22,
                                        style="Bot.TCombobox")
        self.log_choice.pack(side="left", padx=15)
        self._button(bar, "读取最近 150 行", self.load_logs).pack(side="left")
        self.log_text = tk.Text(self.log_tab, bg=SURFACE, fg=TEXT,
                                insertbackground=TEXT, relief="flat", wrap="word",
                                font=("Consolas", 9), state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 12))

    def _build_backups(self):
        bar = tk.Frame(self.backup_tab, bg=BG)
        bar.pack(fill="x", padx=10, pady=(12, 0))
        tk.Label(bar, text="项目备份", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(side="left")
        self._button(bar, "创建完整备份", self.create_backup, True).pack(side="right")
        tk.Label(self.backup_tab,
                 text=("备份保存在本机项目的 backups 目录，包含配置与数据。" if self.is_local
                       else "备份保存在服务器项目的 backups 目录，包含配置、数据与登录目录；不下载到电脑。"),
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=10, pady=(8, 3))
        self.backup_tree = self._tree(self.backup_tab, ("name", "size", "created"),
                                      ("备份文件", "大小", "创建时间"), (520, 110, 180))

    def _build_configs(self):
        tk.Label(self.config_tab, text="配置预览", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w", padx=10, pady=(12, 0))
        tk.Label(self.config_tab,
                 text=("原生项目的配置含密钥，可在 Bot 设置页打开程序向导修改；原始文件不在此页显示。"
                       if self.is_native else
                       "只读查看 Compose 与 settings JSON；含凭据的文件不会发送到桌面。修改请使用项目源码或 AstrBot 控制台。"),
                 bg=BG, fg=MUTED, wraplength=800, justify="left",
                 font=(FONT, 9)).pack(anchor="w", padx=10, pady=(5, 9))
        body = tk.Frame(self.config_tab, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.config_list = tk.Listbox(body, bg=SURFACE, fg=TEXT,
                                      selectbackground="#39594B", relief="flat",
                                      width=33, font=(FONT, 10))
        self.config_list.pack(side="left", fill="y")
        self.config_list.bind("<<ListboxSelect>>", self.load_config)
        self.config_text = tk.Text(body, bg=SURFACE, fg=TEXT,
                                   insertbackground=TEXT, relief="flat",
                                   wrap="none", font=("Consolas", 9), state="disabled")
        self.config_text.pack(side="left", fill="both", expand=True, padx=(8, 0))

    def _build_plugins(self):
        bar = tk.Frame(self.plugin_tab, bg=BG)
        bar.pack(fill="x", padx=10, pady=(12, 0))
        tk.Label(bar, text="已安装插件", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(side="left")
        self._button(bar, "在 AstrBot 控制台管理 ↗", self.open_dashboard).pack(side="right")
        self.plugin_tree = self._tree(self.plugin_tab,
                                      ("id", "name", "version", "description"),
                                      ("目录", "名称", "版本", "简介"),
                                      (240, 180, 90, 360))
        tk.Label(self.plugin_tab,
                 text="插件启停与更新使用 AstrBot 自带管理界面，保持其运行时状态一致。",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=10)

    def _build_bot_settings(self):
        tk.Label(self.bot_tab, text="每个人都用自己的 API 和 Bot 设定", bg=BG, fg=TEXT,
                 font=(FONT, 16, "bold")).pack(anchor="w", padx=14, pady=(17, 4))
        tk.Label(self.bot_tab,
                 text=("在程序内管理自己的模型、角色、聊天规则和可选语音；密钥只写入本机项目。"
                       if self.is_native else
                       "bot助手不预置开发者密钥。下面的入口打开当前项目的 AstrBot 设置；密钥由项目主人在自己的控制台填写。"),
                 bg=BG, fg=MUTED, font=(FONT, 9), wraplength=850,
                 justify="left").pack(anchor="w", padx=14, pady=(0, 10))

        def section(title, description, button, page):
            frame = tk.Frame(self.bot_tab, bg=SURFACE)
            frame.pack(fill="x", padx=12, pady=5, ipady=6)
            tk.Label(frame, text=title, bg=SURFACE, fg=TEXT,
                     font=(FONT, 11, "bold")).pack(anchor="w", padx=15, pady=(7, 2))
            tk.Label(frame, text=description, bg=SURFACE, fg=MUTED,
                     font=(FONT, 9)).pack(anchor="w", padx=15)
            row = tk.Frame(frame, bg=SURFACE)
            row.pack(fill="x", padx=15, pady=(5, 3))
            state = tk.Label(row, text="正在读取…", bg=SURFACE, fg=JADE,
                             font=(FONT, 9))
            state.pack(side="left")
            self._button(row, "在程序中配置" if self.is_native else button,
                         self.configure_native if self.is_native else
                         lambda: self.open_dashboard(page)).pack(side="right")
            return state

        self.api_state = section("API 与模型", "添加自己的模型服务商、API Key 和模型，并选择使用的模型。",
                                 "配置 API ↗", "providers")
        self.bot_state = section("聊天行为", "配置回复规则、触发方式、模型选择和其他 Bot 行为。",
                                 "Bot 配置 ↗", "config")
        self.voice_state = section("语音（可选）", "语音识别与语音回复可分别启用；不需要时保持关闭。",
                                   "设置语音 ↗", "config")
        self.persona_state = section("角色设定", "编写系统提示词、角色性格与开场对话。",
                                     "编辑角色 ↗", "persona")
        thinking = tk.Frame(self.bot_tab, bg=SURFACE)
        thinking.pack(fill="x", padx=12, pady=5, ipady=7)
        tk.Label(thinking, text="思维强度", bg=SURFACE, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w", padx=15, pady=(7, 2))
        tk.Label(thinking, text="按 QQ 账号分别设置关闭、低、高、最高；管理员也可在聊天中直接说。",
                 bg=SURFACE, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=15)
        row = tk.Frame(thinking, bg=SURFACE)
        row.pack(fill="x", padx=15, pady=(5, 3))
        self.thinking_state = tk.Label(row, text="正在读取…", bg=SURFACE, fg=JADE,
                                       font=(FONT, 9))
        self.thinking_state.pack(side="left")
        self._button(row, "调节思维强度", self.open_thinking_window).pack(side="right")

    def open_thinking_window(self):
        dialog = tk.Toplevel(self, bg=BG)
        dialog.title("思维强度 · 按账号设置")
        dialog.geometry("600x350")
        dialog.resizable(False, False)
        dialog.transient(self)
        tk.Label(dialog, text="思维强度", bg=BG, fg=TEXT,
                 font=(FONT, 17, "bold")).pack(anchor="w", padx=24, pady=(21, 5))
        tk.Label(dialog, text="DeepSeek Flash / Pro：关闭、低、高、最高。每个 QQ bot 独立保存。",
                 bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w", padx=24)
        status = tk.Label(dialog, text="正在读取项目…", bg=BG, fg=JADE,
                          font=(FONT, 10), wraplength=550, justify="left")
        status.pack(anchor="w", padx=24, pady=(16, 8))
        form = tk.Frame(dialog, bg=BG)
        form.pack(fill="x", padx=24)
        platform_var = tk.StringVar()
        level_var = tk.StringVar(value="高")
        names = {"none": "关闭", "low": "低", "high": "高", "max": "最高"}
        reverse = {label: key for key, label in names.items()}
        tk.Label(form, text="QQ 平台", bg=BG, fg=MUTED, font=(FONT, 10)).grid(
            row=0, column=0, sticky="w", pady=7)
        platform_choice = ttk.Combobox(form, textvariable=platform_var,
                                        state="readonly", width=31, style="Bot.TCombobox")
        platform_choice.grid(row=0, column=1, padx=(16, 0), pady=7)
        tk.Label(form, text="思维档位", bg=BG, fg=MUTED, font=(FONT, 10)).grid(
            row=1, column=0, sticky="w", pady=7)
        level_choice = ttk.Combobox(form, textvariable=level_var,
                                     values=list(names.values()), state="readonly",
                                     width=31, style="Bot.TCombobox")
        level_choice.grid(row=1, column=1, padx=(16, 0), pady=7)
        platform_levels = {}
        platform_ids = {}
        def selected(_event=None):
            level_var.set(names.get(platform_levels.get(platform_var.get(), "high"), "高"))
        platform_choice.bind("<<ComboboxSelected>>", selected)
        actions = tk.Frame(dialog, bg=BG)
        actions.pack(fill="x", padx=24, pady=(14, 0))
        install_button = self._button(actions, "启用思维调节", lambda: None)
        install_button.pack(side="left")
        save_button = self._button(actions, "保存此账号档位", lambda: None, True)
        save_button.pack(side="right")
        install_button.configure(state="disabled")
        save_button.configure(state="disabled")
        tk.Label(dialog,
                 text="聊天示例：把思考强度调低 / 调到最高 / 关闭思考 / 现在思考强度是多少。\n"
                      "仅 AstrBot 管理员可执行；修改从下一条消息开始生效。",
                 bg=BG, fg=MUTED, justify="left", font=(FONT, 9)).pack(
                     anchor="w", padx=24, pady=(12, 0))

        def loaded(result):
            if not dialog.winfo_exists():
                return
            if not result.get("supported"):
                status.configure(text=result.get("reason", "当前模型不支持思维调节"), fg=AMBER)
                install_button.configure(state="disabled")
                save_button.configure(state="disabled")
                return
            platforms = result.get("platforms", [])
            platform_levels.clear()
            platform_ids.clear()
            for item in platforms:
                label = item.get("label") or item["id"]
                platform_levels[label] = item["level"]
                platform_ids[label] = item["id"]
            platform_choice.configure(values=list(platform_levels))
            if platform_levels and platform_var.get() not in platform_levels:
                platform_var.set(next(iter(platform_levels)))
            selected()
            installed = result.get("installed", False)
            status.configure(text=(f"当前模型：{result['model']}。已启用，可分别保存档位。"
                                   if installed else
                                   f"当前模型：{result['model']}。首次使用需启用组件，AstrBot 将短暂重启。"),
                             fg=JADE)
            install_button.configure(state="disabled" if installed else "normal")
            save_button.configure(state="normal" if installed else "disabled")

        def reload():
            if self.busy:
                if dialog.winfo_exists():
                    dialog.after(250, reload)
                return
            self._request("正在读取思维强度…",
                          lambda: self._call("project_thinking_status"), loaded,
                          lambda error: status.configure(text=error, fg=RED)
                          if dialog.winfo_exists() else None)

        def install():
            source, metadata = bundled_thinking_plugin()
            status.configure(text="正在启用并重启 AstrBot…", fg=AMBER)
            self._request("正在安装思维调节组件…",
                          lambda: self._call("project_thinking_install",
                                             {"source": source, "metadata": metadata}, 180),
                          lambda _result: reload(),
                          lambda error: status.configure(text=error, fg=RED)
                          if dialog.winfo_exists() else None)

        def save():
            platform_id = platform_ids.get(platform_var.get())
            level = reverse.get(level_var.get())
            if not platform_id or not level:
                return
            self._request("正在保存思维档位…",
                          lambda: self._call("project_thinking_set",
                                             {"platform_id": platform_id, "level": level}),
                          lambda _result: (status.configure(text=f"{platform_id} 已设为{names[level]}，下一条消息生效。",
                                                              fg=JADE), reload()),
                          lambda error: status.configure(text=error, fg=RED)
                          if dialog.winfo_exists() else None)

        install_button.configure(command=install)
        save_button.configure(command=save)
        reload()

    def _request(self, note, worker, done, on_error=None):
        if self.busy:
            return
        self.busy = True
        self.note.configure(text=note, fg=MUTED)
        def run():
            try:
                result, error = worker(), None
            except Exception as exc:
                result, error = None, str(exc)
            self.events.put((done, result, error, on_error))
        threading.Thread(target=run, daemon=True).start()

    def _pump(self):
        try:
            while True:
                done, result, error, on_error = self.events.get_nowait()
                self.busy = False
                if error:
                    self.note.configure(text=error, fg=RED)
                    if on_error:
                        on_error(error)
                else:
                    self.note.configure(text="操作完成", fg=JADE)
                    try:
                        done(result)
                    except (KeyError, ValueError, tk.TclError) as exc:
                        self.note.configure(text=f"界面无法显示返回结果：{exc}", fg=RED)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._pump)

    def _call(self, action, payload=None, timeout=45):
        return self.remote.call(action, {"path": self.project_path, **(payload or {})},
                                timeout=timeout)

    def refresh(self):
        def worker():
            status = self._call("project_status")
            backups = self._call("project_backups")
            configs = self._call("project_configs")
            plugins = self._call("project_plugins")
            try:
                bot = self._call("project_bot_settings")
            except RemoteError as exc:
                if exc.code != "usage":
                    raise
                bot = {"unavailable": True}
            try:
                thinking = self._call("project_thinking_status")
            except RemoteError as exc:
                if exc.code != "usage":
                    raise
                thinking = {"unavailable": True}
            return status, backups, configs, plugins, bot, thinking
        def done(results):
            status, backups, configs, plugins, bot, thinking = results
            self.services = [item["name"] for item in status["services"]]
            self.summary.configure(text=f"{status['running']} / {len(self.services)} 个服务运行中")
            self.service_tree.delete(*self.service_tree.get_children())
            for item in status["services"]:
                self.service_tree.insert("", "end", values=(item["name"], item["state"],
                                                           item["status"], item["health"]))
            self.log_choice.configure(values=self.services)
            if self.services and self.log_service.get() not in self.services:
                self.log_service.set("astrbot" if "astrbot" in self.services else self.services[0])
            self.backup_tree.delete(*self.backup_tree.get_children())
            import datetime
            for item in backups["backups"]:
                when = datetime.datetime.fromtimestamp(item["created"]).strftime("%Y-%m-%d %H:%M")
                self.backup_tree.insert("", "end", values=(item["name"],
                                        f"{item['bytes'] / 1024 / 1024:.1f} MB", when))
            self.config_list.delete(0, "end")
            for item in configs["configs"]:
                self.config_list.insert("end", item["path"])
            self.plugin_tree.delete(*self.plugin_tree.get_children())
            for item in plugins["plugins"]:
                self.plugin_tree.insert("", "end", values=(item["id"], item["name"],
                                                          item["version"], item["description"]))
            if bot.get("unavailable"):
                for label in (self.api_state, self.bot_state, self.voice_state,
                              self.persona_state):
                    label.configure(text="请在连接设置中重新连接以更新管理组件")
            else:
                self.api_state.configure(text=(f"已启用 {bot['provider_count']} 个模型配置"
                                               if bot["provider_count"] else "尚未配置模型"))
                self.bot_state.configure(text=("聊天已启用" if bot["chat_enabled"]
                                               else "聊天未启用或项目尚未初始化"))
                voice = ("语音识别开" if bot["stt_enabled"] else "语音识别关") + " · " + (
                    "语音回复开" if bot["tts_enabled"] else "语音回复关")
                self.voice_state.configure(text=voice)
                self.persona_state.configure(text=("已选择默认角色" if bot["persona_selected"]
                                                   else "使用默认角色或尚未选择"))
            if thinking.get("unavailable"):
                self.thinking_state.configure(text="请更新服务器管理组件")
            elif not thinking.get("supported"):
                self.thinking_state.configure(text=thinking.get("reason", "当前模型不支持"))
            elif thinking.get("installed"):
                summary = " · ".join(f"{item.get('label', item['id'])}：{item['level']}"
                                     for item in thinking.get("platforms", []))
                self.thinking_state.configure(text=summary or "已启用")
            else:
                self.thinking_state.configure(text="可启用；当前默认高档")
        self._request("正在读取项目服务、备份、配置和插件…", worker, done)

    def selected_service(self):
        selection = self.service_tree.selection()
        return self.service_tree.item(selection[0], "values")[0] if selection else ""

    def selected_logs(self):
        name = self.selected_service()
        if name:
            self.log_service.set(name)
            self.load_logs()

    def load_logs(self):
        name = self.log_service.get()
        if not name:
            return
        def done(result):
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("1.0", result["text"] or "暂无日志")
            self.log_text.configure(state="disabled")
        self._request(f"正在读取 {name} 日志…",
                      lambda: self._call("project_logs", {"service": name, "lines": 150}), done)

    def restart_selected(self):
        name = self.selected_service()
        if not name:
            self.note.configure(text="请先选择一个服务", fg=AMBER)
            return
        warning = ("\nNapCat 重启后可能需要重新扫码。" if "napcat" in name.lower() else "")
        if not messagebox.askyesno("重启服务", f"确认只重启 {name}？{warning}", parent=self):
            return
        self._request(f"正在重启 {name}…",
                      lambda: self._call("project_restart", {"service": name}, 150),
                      lambda _result: self.refresh())

    def create_backup(self):
        location = "本机项目" if self.is_local else "服务器"
        if not messagebox.askyesno("创建项目备份",
                                   f"将在{location} backups 目录保存整个项目（可能包含凭据）。\n"
                                   "大型项目可能需要几分钟。确认开始？", parent=self):
            return
        def done(result):
            self.note.configure(text=f"已在{location}保存 {result['name']}", fg=JADE)
            self.refresh()
        self._request("正在备份项目，请勿关闭窗口…",
                      lambda: self._call("project_backup", timeout=900), done)

    def load_config(self, _event=None):
        selection = self.config_list.curselection()
        if not selection:
            return
        name = self.config_list.get(selection[0])
        def done(result):
            self.config_text.configure(state="normal")
            self.config_text.delete("1.0", "end")
            self.config_text.insert("1.0", result["text"])
            self.config_text.configure(state="disabled")
        self._request(f"正在读取 {name}…",
                      lambda: self._call("project_config_read", {"config": name}), done)
