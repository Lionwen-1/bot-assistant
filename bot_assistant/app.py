"""Windows desktop control desk for self-hosted QQ logins."""

from __future__ import annotations

import base64
import io
import json
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageDraw, ImageOps, ImageTk

from . import __version__
from .avatars import avatar_path, fetch_avatar, needs_refresh
from .config import LocalProfile, NativeProfile, Profile, config_home, load_profile, save_profile
from .docker_setup import DockerSetupError, ensure_docker_ready
from .local import Local
from .native import NativeLocal
from .native_settings import BotSettings
from .remote import HostKey, Remote, RemoteError, bundled_manager_path, bundled_thinking_plugin
from .project_ui import ProjectWindow

BG = "#101815"
SURFACE = "#1C2923"
SURFACE_2 = "#273B31"
TEXT = "#FAF7ED"
MUTED = "#ADC0B3"
FAINT = "#789081"
AMBER = "#F1B45B"
JADE = "#88D5BA"
RED = "#EF9186"
WHITE = "#FFFFFF"
FONT = "Microsoft YaHei UI"
LATIN = "Segoe UI"
CARD_ROW_HEIGHT = 155
MAX_VISIBLE_ROWS = 2
EMPTY_LIST_HEIGHT = 106


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "assets" / name


def rounded(c: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
            radius: int, fill: str, tag: str = "") -> None:
    c.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill,
                       outline="", tags=tag)
    c.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill,
                       outline="", tags=tag)
    for cx, cy in ((x1+radius, y1+radius), (x2-radius, y1+radius),
                   (x1+radius, y2-radius), (x2-radius, y2-radius)):
        c.create_oval(cx-radius, cy-radius, cx+radius, cy+radius,
                      fill=fill, outline="", tags=tag)


def header_art(width: int = 900, height: int = 178) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            glow = max(0.0, 1.0 - (((x-735)/365)**2 + ((y-15)/205)**2)**0.5)
            pixels[x, y] = (int(23+28*glow), int(41+22*glow), int(33+8*glow))
    draw = ImageDraw.Draw(image, "RGBA")
    for radius, alpha in ((180, 14), (128, 18), (80, 23)):
        draw.ellipse((730-radius, 17-radius, 730+radius, 17+radius),
                     outline=(241, 180, 91, alpha), width=2)
    return image


def center_dialog(dialog: tk.Toplevel, parent: tk.Tk, width: int, height: int) -> None:
    parent.update_idletasks()
    x = max(0, parent.winfo_rootx() + (parent.winfo_width() - width) // 2)
    y = max(0, parent.winfo_rooty() + (parent.winfo_height() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")


class AccountCard(tk.Canvas):
    def __init__(self, parent: tk.Widget, account: dict, app: "App"):
        super().__init__(parent, width=401, height=143, bg=BG,
                         highlightthickness=0, bd=0)
        self.account = account
        self.app = app
        self.portrait = None
        self.menu = tk.Menu(self, tearoff=False, bg=SURFACE, fg=TEXT,
                            activebackground=SURFACE_2, activeforeground=TEXT)
        self.menu.add_command(label="修改昵称", command=lambda: app.rename_account(account))
        if account.get("astrbot_ws_port"):
            self.menu.add_command(label="对接 AstrBot", command=lambda: app.show_local_connection(account))
        self.menu.add_command(label="移除账号", command=lambda: app.remove_account(account))
        self.bind("<Button-1>", self.clicked)
        self.bind("<Motion>", self.hover)
        self.draw()

    def set_avatar(self, path: Path) -> None:
        with Image.open(path) as source:
            face = ImageOps.fit(source.convert("RGB"), (52, 52),
                                method=Image.Resampling.LANCZOS)
        picture = Image.new("RGBA", (56, 56))
        draw = ImageDraw.Draw(picture)
        draw.ellipse((0, 0, 55, 55), fill=AMBER)
        mask = Image.new("L", (52, 52))
        ImageDraw.Draw(mask).ellipse((0, 0, 51, 51), fill=255)
        picture.paste(face, (2, 2), mask)
        self.portrait = ImageTk.PhotoImage(picture)
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        state = self.account.get("state", "unknown")
        rounded(self, 0, 0, 401, 143, 20, SURFACE)
        self.create_oval(282, -99, 485, 97, fill=SURFACE_2, outline="")
        if self.portrait is None:
            rounded(self, 17, 18, 73, 74, 17, "#34443A")
            self.create_text(45, 46, text=self.account["name"][:1], fill=AMBER,
                             font=(FONT, 20, "bold"))
        else:
            self.create_image(17, 18, image=self.portrait, anchor="nw")
        self.create_text(88, 26, text=self.account["name"][:16], fill=TEXT,
                         anchor="nw", font=(FONT, 16, "bold"))
        self.create_text(89, 56, text=self.account["qq"], fill=MUTED,
                         anchor="nw", font=(LATIN, 10))
        self.create_text(374, 28, text="···", fill=MUTED,
                         font=(LATIN, 17, "bold"), tags="menu")
        label, color = {"online": ("在线运行", JADE),
                        "offline": ("需要登录", RED)}.get(
                            state, ("状态待确认", AMBER))
        self.create_oval(18, 111, 27, 120, fill=color, outline="")
        self.create_text(35, 106, text=label, anchor="nw", fill=color,
                         font=(FONT, 10, "bold"))
        if state != "online":
            rounded(self, 258, 97, 382, 135, 10, AMBER, "prepare")
            self.create_text(320, 116,
                             text="获取登录码" if state == "offline" else "检查登录码",
                             fill=BG,
                             font=(FONT, 10, "bold"), tags="prepare")

    def clicked(self, event) -> None:
        if event.x >= 352 and event.y <= 52:
            self.menu.tk_popup(event.x_root, event.y_root)
        elif (self.account.get("state") != "online" and 258 <= event.x <= 382
              and 97 <= event.y <= 135):
            self.app.prepare(self.account["qq"])

    def hover(self, event) -> None:
        clickable = (event.x >= 352 and event.y <= 52) or (
            self.account.get("state") != "online" and 258 <= event.x <= 382
            and 97 <= event.y <= 135)
        self.configure(cursor="hand2" if clickable else "")


class App:
    def __init__(self, root: tk.Tk, demo: bool = False):
        self.root = root
        self.demo = demo
        self.events: queue.Queue[tuple] = queue.Queue()
        self.profile: Profile | LocalProfile | NativeProfile | None = None
        self.remote: Remote | Local | NativeLocal | None = None
        self.accounts: list[dict] = []
        self.cards: dict[str, AccountCard] = {}
        self.avatar_attempts: dict[str, float] = {}
        self.busy = False
        self.qr_target = ""
        self.qr_name = ""
        self.qr_image = None
        self.qr_ttl = 0
        self.qr_age = 0
        self.qr_started = 0.0
        self.setup_dialog: tk.Toplevel | None = None
        self.native_status_label: tk.Label | None = None
        self.project_window: ProjectWindow | None = None
        self.dashboard_process = None
        self.dashboard_local_port = 0
        root.title("bot助手 · 连接与项目运维")
        x = max(0, (root.winfo_screenwidth() - 900) // 2)
        y = max(0, (root.winfo_screenheight() - 960) // 2)
        root.geometry(f"900x920+{x}+{y}")
        root.minsize(900, 640)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if resource_path("bot-assistant.ico").exists():
            try:
                root.iconbitmap(str(resource_path("bot-assistant.ico")))
            except tk.TclError:
                pass
        self.build()
        self.apply_accounts([])
        if demo:
            self.apply_accounts([
                {"qq": "123456789", "name": "示例账号", "state": "online", "detail": "QQ 已登录"},
                {"qq": "987654321", "name": "第二个账号", "state": "offline", "detail": "等待 QQ 登录"},
                {"qq": "123123123", "name": "更多账号", "state": "unknown", "detail": "接口尚未就绪"}])
            self.say("演示模式 · 不连接任何服务器")
        else:
            try:
                self.profile = load_profile()
            except ValueError as exc:
                self.say(str(exc))
            if self.profile:
                self.remote = (NativeLocal(self.profile) if isinstance(self.profile, NativeProfile)
                               else Local(self.profile) if isinstance(self.profile, LocalProfile)
                               else Remote(self.profile))
                root.after(300, self.refresh)
            else:
                root.after(450, self.open_setup)
        root.after(100, self.pump)
        root.after(1000, self.tick)
        root.after(30000, self.auto_refresh)

    def build(self) -> None:
        header = tk.Canvas(self.root, width=900, height=178, bg=BG,
                           highlightthickness=0)
        header.pack(fill="x")
        self.header_image = ImageTk.PhotoImage(header_art())
        header.create_image(0, 0, image=self.header_image, anchor="nw")
        logo = resource_path("bot-assistant.png")
        if logo.exists():
            with Image.open(logo) as source:
                self.logo_image = ImageTk.PhotoImage(
                    source.convert("RGBA").resize((56, 56), Image.Resampling.LANCZOS))
            header.create_image(31, 25, image=self.logo_image, anchor="nw")
        header.create_text(105, 29, text="BOT ASSISTANT  /  PROJECT DESK",
                           fill=AMBER, anchor="nw", font=(LATIN, 10, "bold"))
        header.create_text(30, 85, text="让每个连接，都清晰可见。", fill=TEXT,
                           anchor="nw", font=(FONT, 23, "bold"))
        header.create_text(32, 143, text="本机或远程 · QQ 连接与 AstrBot 项目管理",
                           fill=MUTED, anchor="nw", font=(FONT, 10))
        rounded(header, 569, 26, 713, 67, 12, "#334B3C", "project")
        header.create_text(641, 46, text="◈  项目运维", fill=TEXT,
                           font=(FONT, 10, "bold"), tags="project")
        header.tag_bind("project", "<Button-1>", lambda _e: self.open_project())
        header.tag_bind("project", "<Enter>", lambda _e: header.configure(cursor="hand2"))
        header.tag_bind("project", "<Leave>", lambda _e: header.configure(cursor=""))
        rounded(header, 726, 26, 865, 67, 12, "#334B3C", "settings")
        header.create_text(795, 46, text="⚙  连接设置", fill=TEXT,
                           font=(FONT, 10, "bold"), tags="settings")
        header.tag_bind("settings", "<Button-1>", lambda _e: self.open_setup())
        header.tag_bind("settings", "<Enter>", lambda _e: header.configure(cursor="hand2"))
        header.tag_bind("settings", "<Leave>", lambda _e: header.configure(cursor=""))

        toolbar = tk.Frame(self.root, bg=BG)
        toolbar.pack(fill="x", padx=30, pady=(12, 8))
        self.account_title = tk.Label(toolbar, text="我的账号  0", bg=BG, fg=TEXT,
                                      font=(FONT, 13, "bold"))
        self.account_title.pack(side="left")
        self.check_label = tk.Label(toolbar, text="尚未连接", bg=BG,
                                    fg=FAINT, font=(FONT, 9))
        self.check_label.pack(side="left", padx=18)
        self.add_button = tk.Button(toolbar, text="＋  添加账号", command=self.open_add,
                                    bg=AMBER, fg=BG, activebackground="#F7C983",
                                    activeforeground=BG, bd=0, padx=16, pady=7,
                                    font=(FONT, 10, "bold"), cursor="hand2")
        self.add_button.pack(side="right")
        tk.Button(toolbar, text="↻  刷新", command=self.refresh, bg=SURFACE_2,
                  fg=TEXT, activebackground="#3A5143", activeforeground=TEXT,
                  bd=0, padx=14, pady=7, font=(FONT, 10),
                  cursor="hand2").pack(side="right", padx=(0, 9))
        tk.Button(toolbar, text="Bot 设置", command=lambda: self.open_project(settings=True),
                  bg=SURFACE_2, fg=TEXT, activebackground="#3A5143",
                  activeforeground=TEXT, bd=0, padx=14, pady=7,
                  font=(FONT, 10), cursor="hand2").pack(side="right", padx=(0, 9))

        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="x", padx=30)
        self.list_canvas = tk.Canvas(shell, height=EMPTY_LIST_HEIGHT, bg=BG,
                                     highlightthickness=0, bd=0)
        self.list_scrollbar = tk.Scrollbar(
            shell, orient="vertical", command=self.list_canvas.yview,
            bg=SURFACE_2, troughcolor=BG, activebackground=JADE,
            bd=0, highlightthickness=0)
        self.list_canvas.configure(yscrollcommand=self.list_scrollbar.set)
        self.list_canvas.pack(side="left", fill="x", expand=True)
        self.card_frame = tk.Frame(self.list_canvas, bg=BG)
        self.card_window = self.list_canvas.create_window((0, 0),
                                                           window=self.card_frame, anchor="nw")
        self.card_frame.bind("<Configure>", lambda _e: self.list_canvas.configure(
            scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda event: self.list_canvas.itemconfigure(
            self.card_window, width=event.width))
        self.list_canvas.bind("<MouseWheel>", lambda event: self.list_canvas.yview_scroll(
            int(-event.delta / 120), "units"))

        tk.Label(self.root, text="登录与恢复", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w", padx=30, pady=(3, 6))
        self.qr_panel = tk.Canvas(self.root, width=840, height=226, bg=BG,
                                  highlightthickness=0)
        self.qr_panel.pack(padx=30)
        self.draw_qr()
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=31, pady=(8, 8))
        tk.Label(footer, text="最近操作", bg=BG, fg=TEXT,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        self.message = tk.Label(footer, text="准备就绪", bg=BG, fg=MUTED,
                                anchor="w", font=(FONT, 9), wraplength=820)
        self.message.pack(fill="x", pady=(6, 0))

    def say(self, text: str) -> None:
        self.message.configure(text=f"{time.strftime('%H:%M')}   {text}")

    def task(self, note: str, fn, done, on_error=None) -> None:
        if self.busy:
            return
        self.busy = True
        if note:
            self.say(note)

        def worker():
            try:
                self.events.put(("task", done, on_error, fn(), None))
            except Exception as exc:
                self.events.put(("task", done, on_error, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "task":
                    _, callback, error_callback, result, error = event
                    self.busy = False
                    if error:
                        self.say(str(error))
                        if error_callback:
                            error_callback(error)
                    else:
                        try:
                            callback(result)
                        except (KeyError, ValueError, OSError, tk.TclError) as exc:
                            self.say(f"返回数据无法处理：{exc}")
                elif event[0] == "hostkey":
                    _, remote, key = event
                    self.confirm_host_key(remote, key)
                elif event[0] == "avatar":
                    _, qq, path = event
                    if qq in self.cards:
                        try:
                            self.cards[qq].set_avatar(path)
                        except (OSError, ValueError):
                            pass
                elif event[0] == "docker_progress":
                    self.say(event[1])
                    if self.setup_dialog and self.setup_dialog.winfo_exists():
                        self.setup_status.configure(text=event[1], fg=MUTED)
                    if self.native_status_label and self.native_status_label.winfo_exists():
                        self.native_status_label.configure(text=event[1], fg=MUTED)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def tick(self) -> None:
        if self.qr_target:
            self.draw_qr()
        self.root.after(1000, self.tick)

    def auto_refresh(self) -> None:
        if self.remote and not self.busy:
            self.refresh(quiet=True)
        self.root.after(30000, self.auto_refresh)

    def apply_accounts(self, accounts: list[dict]) -> None:
        self.accounts = accounts
        if self.qr_target and any(item["qq"] == self.qr_target and
                                  item["state"] == "online" for item in accounts):
            self.clear_qr()
        for child in self.card_frame.winfo_children():
            child.destroy()
        self.cards = {}
        title = "演示账号" if self.demo else "我的账号"
        self.account_title.configure(text=f"{title}  {len(accounts)}")
        if not accounts:
            tk.Label(self.card_frame, text="还没有账号。点击右上角添加第一个 QQ 号。",
                     bg=BG, fg=MUTED, font=(FONT, 11)).grid(
                         row=0, column=0, sticky="w", padx=10, pady=40)
        for index, account in enumerate(accounts):
            card = AccountCard(self.card_frame, account, self)
            card.grid(row=index // 2, column=index % 2,
                      padx=(0, 14) if index % 2 == 0 else (0, 0), pady=(0, 12))
            self.cards[account["qq"]] = card
            path = avatar_path(account["qq"])
            if path.exists():
                try:
                    card.set_avatar(path)
                except (OSError, ValueError):
                    pass
            if (needs_refresh(account["qq"])
                and time.time() - self.avatar_attempts.get(account["qq"], 0) > 600):
                self.avatar_attempts[account["qq"]] = time.time()
                threading.Thread(target=self._avatar_worker,
                                 args=(account["qq"],), daemon=True).start()
        rows = (len(accounts) + 1) // 2
        list_height = (EMPTY_LIST_HEIGHT if not rows else
                       min(rows, MAX_VISIBLE_ROWS) * CARD_ROW_HEIGHT)
        self.list_canvas.configure(height=list_height)
        if rows > MAX_VISIBLE_ROWS:
            if not self.list_scrollbar.winfo_manager():
                self.list_scrollbar.pack(side="right", fill="y")
        else:
            self.list_scrollbar.pack_forget()
            self.list_canvas.yview_moveto(0)
        self.root.update_idletasks()
        desired_height = min(self.root.winfo_reqheight() + 8,
                             self.root.winfo_screenheight() - 80)
        if self.root.winfo_height() != desired_height:
            self.root.geometry(f"900x{desired_height}")
        self.check_label.configure(text=f"上次检查  {time.strftime('%H:%M:%S')}")
        self.draw_qr()

    def _avatar_worker(self, qq: str) -> None:
        try:
            self.events.put(("avatar", qq, fetch_avatar(qq)))
        except (OSError, ValueError):
            pass

    def refresh(self, quiet: bool = False) -> None:
        if self.demo:
            return
        if not self.remote:
            self.open_setup()
            return
        def done(result):
            self.apply_accounts(result["accounts"])
            if not quiet:
                self.say("账号状态已更新")
        self.task("" if quiet else "正在读取账号状态…",
                  lambda: self.remote.call("list"), done)

    def open_setup(self) -> None:
        if self.demo:
            messagebox.showinfo("演示模式", "演示模式不会连接 Docker 或服务器。", parent=self.root)
            return
        if self.setup_dialog and self.setup_dialog.winfo_exists():
            self.setup_dialog.lift()
            return
        dialog = tk.Toplevel(self.root, bg=BG)
        self.setup_dialog = dialog
        dialog.title("连接设置")
        center_dialog(dialog, self.root, 590, 590)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(dialog, text="选择部署位置", bg=BG, fg=TEXT,
                 font=(FONT, 17, "bold")).pack(anchor="w", padx=28, pady=(26, 5))
        tk.Label(dialog, text="免 Docker 可直接在 Windows 部署；也可继续使用 Docker 或远程 SSH。",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=29)
        mode = tk.StringVar(value=("ssh" if isinstance(self.profile, Profile) else
                                   "local" if isinstance(self.profile, LocalProfile) else "native"))
        selector = tk.Frame(dialog, bg=BG)
        selector.pack(fill="x", padx=29, pady=(16, 0))
        for label, value in (("本机免 Docker", "native"), ("本机 Docker", "local"),
                             ("远程 SSH", "ssh")):
            tk.Radiobutton(selector, text=label, value=value, variable=mode,
                           bg=BG, fg=TEXT, selectcolor=SURFACE, activebackground=BG,
                           activeforeground=TEXT, font=(FONT, 10)).pack(side="left", padx=(0, 25))
        local_form = tk.Frame(dialog, bg=BG)
        native_form = tk.Frame(dialog, bg=BG)
        remote_form = tk.Frame(dialog, bg=BG)
        profile = self.profile
        try:
            ssh_profile = profile if isinstance(profile, Profile) else load_profile("ssh")
            local_profile = profile if isinstance(profile, LocalProfile) else load_profile("local")
            native_profile = profile if isinstance(profile, NativeProfile) else load_profile("native")
        except ValueError:
            ssh_profile, local_profile, native_profile = None, None, None
        fields = {}
        for row, (label, value) in enumerate((
            ("服务器地址", ssh_profile.host if ssh_profile else ""),
            ("SSH 端口", str(ssh_profile.port) if ssh_profile else "22"),
            ("SSH 用户", ssh_profile.user if ssh_profile else ""),
            ("私钥文件", ssh_profile.key_path if ssh_profile else ""),
            ("AstrBot 项目目录", ssh_profile.project_path if ssh_profile else ""),
            ("AstrBot 面板端口", str(ssh_profile.dashboard_port) if ssh_profile else "6185"),
        )):
            tk.Label(remote_form, text=label, bg=BG, fg=MUTED,
                     font=(FONT, 10)).grid(row=row, column=0, sticky="w", pady=7)
            entry = tk.Entry(remote_form, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                             relief="flat", width=39, font=(LATIN, 10))
            entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="ew", padx=(16, 6), ipady=7)
            fields[label] = entry
        tk.Button(remote_form, text="浏览", bg=SURFACE_2, fg=TEXT, bd=0,
                  command=lambda: self._browse_key(fields["私钥文件"])).grid(
                      row=3, column=2, padx=(2, 0))
        local_path = tk.Entry(local_form, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                              relief="flat", width=39, font=(LATIN, 10))
        local_path.insert(0, local_profile.project_path if local_profile
                          else str(config_home() / "local-project"))
        local_port = tk.Entry(local_form, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                              relief="flat", width=39, font=(LATIN, 10))
        local_port.insert(0, str(local_profile.dashboard_port) if local_profile
                          else "6185")
        for row, (label, entry) in enumerate((("本机项目目录", local_path),
                                               ("AstrBot 面板端口", local_port))):
            tk.Label(local_form, text=label, bg=BG, fg=MUTED,
                     font=(FONT, 10)).grid(row=row, column=0, sticky="w", pady=8)
            entry.grid(row=row, column=1, sticky="ew", padx=(16, 6), ipady=7)
        def browse_local():
            path = filedialog.askdirectory(title="选择本机 AstrBot 项目目录")
            if path:
                local_path.delete(0, "end")
                local_path.insert(0, path)
        tk.Button(local_form, text="浏览", bg=SURFACE_2, fg=TEXT, bd=0,
                  command=browse_local).grid(row=0, column=2, padx=(2, 0))
        native_path = tk.Entry(native_form, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                               relief="flat", width=39, font=(LATIN, 10))
        native_path.insert(0, native_profile.project_path if native_profile
                           else str(config_home() / "native-project"))
        native_port = tk.Entry(native_form, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                               relief="flat", width=39, font=(LATIN, 10))
        native_port.insert(0, str(native_profile.dashboard_port) if native_profile else "6185")
        for row, (label, entry) in enumerate((("本机项目目录", native_path),
                                               ("AstrBot 面板端口", native_port))):
            tk.Label(native_form, text=label, bg=BG, fg=MUTED,
                     font=(FONT, 10)).grid(row=row, column=0, sticky="w", pady=8)
            entry.grid(row=row, column=1, sticky="ew", padx=(16, 6), ipady=7)
        tk.Button(native_form, text="浏览", bg=SURFACE_2, fg=TEXT, bd=0,
                  command=lambda: self._browse_directory(native_path)).grid(row=0, column=2)
        def show_mode(*_args):
            remote_form.pack_forget()
            local_form.pack_forget()
            native_form.pack_forget()
            (native_form if mode.get() == "native" else
             local_form if mode.get() == "local" else remote_form).pack(
                fill="x", padx=29, pady=(20, 0), before=self.setup_status)
            self.setup_status.configure(
                text=("首次联网下载 AstrBot、NapCat 与腾讯 QQ 官方组件，无需 Docker。"
                      "下载文件经校验后只部署在你选择的目录。") if mode.get() == "native" else
                ("一个 EXE 完成本机部署：首次运行会从 Docker 官网下载安装程序，"
                      "启动 Linux 引擎并部署 AstrBot；请在弹出的官方窗口确认协议。"
                      "下载需要网络，WSL 首次启用可能需要重启。") if mode.get() == "local"
                else "首次连接时，请核对服务器 SSH 指纹。")
            self.setup_button.configure(text=("配置并部署" if mode.get() == "native" else
                                              "一键本机部署" if mode.get() == "local" else "检查并启用"))
        self.setup_status = tk.Label(dialog, text="",
                                     bg=BG, fg=FAINT, font=(FONT, 9),
                                     wraplength=470, justify="left")
        self.setup_status.pack(anchor="w", padx=29, pady=(18, 10))
        self.setup_button = tk.Button(dialog, text="检查并启用", bg=AMBER, fg=BG,
                                      bd=0, padx=26, pady=8, cursor="hand2",
                                      font=(FONT, 10, "bold"),
                                      command=lambda: self.connect_native(native_path.get(), native_port.get())
                                      if mode.get() == "native" else
                                      self.connect_local(local_path, local_port)
                                      if mode.get() == "local" else self.connect(fields))
        self.setup_button.pack(anchor="e", padx=29)
        mode.trace_add("write", show_mode)
        show_mode()

    @staticmethod
    def _browse_key(entry: tk.Entry) -> None:
        path = filedialog.askopenfilename(title="选择 SSH 私钥")
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    @staticmethod
    def _browse_directory(entry: tk.Entry) -> None:
        path = filedialog.askdirectory(title="选择独立项目目录")
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def connect_native(self, path: str, port: str) -> None:
        try:
            profile = NativeProfile(path.strip(), int(port.strip()))
            profile.validate()
        except (ValueError, TypeError) as exc:
            self.setup_status.configure(text=str(exc), fg=RED)
            return
        self.open_native_settings(profile)

    def open_native_settings(self, profile: NativeProfile | None = None) -> None:
        editing = profile is None
        if editing:
            if not isinstance(self.remote, NativeLocal):
                return
            profile = self.remote.profile
        try:
            profile.validate()
            backend = NativeLocal(profile)
            defaults = backend.current_settings() if editing else {}
        except (ValueError, OSError) as exc:
            self.say(str(exc))
            return
        dialog = tk.Toplevel(self.root, bg=BG)
        dialog.title("bot助手 · 完整配置")
        center_dialog(dialog, self.root, 640, 730)
        dialog.minsize(600, 550)
        dialog.transient(self.setup_dialog if self.setup_dialog and self.setup_dialog.winfo_exists()
                         else self.root)
        dialog.grab_set()
        tk.Label(dialog, text="配置你的 Bot", bg=BG, fg=TEXT,
                 font=(FONT, 18, "bold")).pack(anchor="w", padx=25, pady=(18, 3))
        tk.Label(dialog, text="模型和语音 API 由你填写；密钥只写入此本机项目。语音完全可选。",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=26)
        canvas = tk.Canvas(dialog, bg=BG, highlightthickness=0)
        bar = tk.Scrollbar(dialog, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y", pady=(10, 65))
        canvas.pack(fill="both", expand=True, padx=(20, 0), pady=(10, 64))
        frame = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        fields = {}
        def heading(label):
            tk.Label(frame, text=label, bg=BG, fg=AMBER,
                     font=(FONT, 11, "bold")).pack(anchor="w", pady=(17, 5))
        def entry(key, label, value="", secret=False):
            tk.Label(frame, text=label, bg=BG, fg=MUTED,
                     font=(FONT, 9)).pack(anchor="w", pady=(7, 3))
            widget = tk.Entry(frame, width=65, bg=SURFACE, fg=TEXT, relief="flat",
                              insertbackground=TEXT, show="●" if secret else "")
            widget.insert(0, defaults.get(key, value))
            widget.pack(anchor="w", ipady=7)
            fields[key] = widget
        heading("01 · 模型 API")
        entry("api_base", "兼容 OpenAI 的 API 地址（HTTPS）")
        entry("api_key", "API Key（编辑时留空表示保留原密钥）", secret=True)
        entry("model", "模型名称，例如 gpt-4o-mini")
        heading("02 · 角色设定")
        entry("persona_name", "角色名称", "我的 Bot")
        tk.Label(frame, text="角色系统提示词", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", pady=(7, 3))
        persona = tk.Text(frame, width=65, height=6, bg=SURFACE, fg=TEXT,
                          relief="flat", insertbackground=TEXT, wrap="word")
        persona.insert("1.0", defaults.get("persona_prompt", ""))
        persona.pack(anchor="w")
        heading("03 · 聊天规则")
        chat_enabled = tk.BooleanVar(value=bool(defaults.get("chat_enabled", True)))
        friend_needs_prefix = tk.BooleanVar(value=bool(defaults.get("friend_needs_prefix", False)))
        reply_with_mention = tk.BooleanVar(value=bool(defaults.get("reply_with_mention", False)))
        reply_with_quote = tk.BooleanVar(value=bool(defaults.get("reply_with_quote", False)))
        for text_value, variable in (("启用模型聊天", chat_enabled),
                                     ("私聊必须带唤醒词", friend_needs_prefix),
                                     ("群聊回复时 @ 提问者", reply_with_mention),
                                     ("回复时引用原消息", reply_with_quote)):
            tk.Checkbutton(frame, text=text_value, variable=variable, bg=BG, fg=TEXT,
                           selectcolor=SURFACE, activebackground=BG,
                           activeforeground=TEXT).pack(anchor="w")
        entry("wake_prefix", "唤醒词（可选，例如 /bot）")
        heading("04 · 语音识别（可选）")
        stt_enabled = tk.BooleanVar(value=bool(defaults.get("stt_enabled", False)))
        tk.Checkbutton(frame, text="接收语音时转文字", variable=stt_enabled,
                       bg=BG, fg=TEXT, selectcolor=SURFACE,
                       activebackground=BG, activeforeground=TEXT).pack(anchor="w")
        entry("stt_base", "语音识别 API 地址")
        entry("stt_key", "语音识别 API Key（可与模型 Key 相同）", secret=True)
        entry("stt_model", "语音识别模型", "whisper-1")
        heading("05 · 语音回复（可选）")
        tts_enabled = tk.BooleanVar(value=bool(defaults.get("tts_enabled", False)))
        tk.Checkbutton(frame, text="允许语音回复", variable=tts_enabled,
                       bg=BG, fg=TEXT, selectcolor=SURFACE,
                       activebackground=BG, activeforeground=TEXT).pack(anchor="w")
        entry("tts_base", "语音合成 API 地址")
        entry("tts_key", "语音合成 API Key", secret=True)
        entry("tts_model", "语音合成模型", "tts-1")
        entry("tts_voice", "音色", "alloy")
        heading("06 · 本机控制台")
        entry("dashboard_password", "AstrBot 控制台密码（至少 12 位；编辑角色时也需填写）",
              secret=True)
        footer = tk.Frame(dialog, bg=BG)
        footer.place(relx=0, rely=1, relwidth=1, height=61, anchor="sw")
        note = tk.Label(footer, text="首次部署需联网，约下载 550 MB。", bg=BG, fg=MUTED,
                        font=(FONT, 9), wraplength=400, justify="left")
        note.pack(side="left", padx=24)
        self.native_status_label = note
        def submit():
            if self.busy:
                return
            values = {name: widget.get().strip() for name, widget in fields.items()}
            settings = BotSettings(api_base=values["api_base"], api_key=values["api_key"],
                                   model=values["model"], persona_name=values["persona_name"],
                                   persona_prompt=persona.get("1.0", "end-1c").strip(),
                                   chat_enabled=chat_enabled.get(),
                                   wake_prefix=values["wake_prefix"],
                                   friend_needs_prefix=friend_needs_prefix.get(),
                                   reply_with_mention=reply_with_mention.get(),
                                   reply_with_quote=reply_with_quote.get(),
                                   stt_enabled=stt_enabled.get(), stt_base=values["stt_base"],
                                   stt_key=values["stt_key"], stt_model=values["stt_model"],
                                   tts_enabled=tts_enabled.get(), tts_base=values["tts_base"],
                                   tts_key=values["tts_key"], tts_model=values["tts_model"],
                                   tts_voice=values["tts_voice"])
            password = values["dashboard_password"]
            try:
                existing = (json.loads((backend.root / "data" / "cmd_config.json").read_text(
                    encoding="utf-8-sig")) if editing else {})
                settings.validate(existing=existing)
                if not editing and len(password) < 12:
                    raise ValueError("请设置至少 12 位的控制台密码")
            except (ValueError, OSError) as exc:
                note.configure(text=str(exc), fg=RED)
                return
            button.configure(state="disabled")
            progress = lambda message: self.events.put(("docker_progress", message))
            def work():
                if editing:
                    backend.update_settings(settings, password, progress)
                else:
                    backend.preflight()
                    backend.deploy(settings, password, progress)
                return backend
            def done(connected):
                if dialog.winfo_exists(): dialog.destroy()
                if editing:
                    self.say("Bot 设置已保存，AstrBot 已更新")
                    if self.project_window and self.project_window.winfo_exists():
                        self.project_window.refresh()
                else:
                    self._activate_backend(connected, "本机免 Docker 项目已部署")
            def failed(error):
                if dialog.winfo_exists():
                    note.configure(text=str(error), fg=RED)
                    button.configure(state="normal")
            self.task("正在准备本机项目…", work, done, failed)
        button = tk.Button(footer, text="保存设置" if editing else "下载并部署",
                           command=submit, bg=AMBER, fg=BG, bd=0, padx=18, pady=8,
                           font=(FONT, 10, "bold"))
        button.pack(side="right", padx=22, pady=10)

    def connect(self, fields: dict[str, tk.Entry]) -> None:
        if self.busy:
            self.setup_status.configure(text="请等待当前操作完成后重试", fg=AMBER)
            return
        try:
            profile = Profile(fields["服务器地址"].get().strip(),
                              int(fields["SSH 端口"].get().strip()),
                              fields["SSH 用户"].get().strip(),
                              fields["私钥文件"].get().strip(),
                              fields["AstrBot 项目目录"].get().strip(),
                              int(fields["AstrBot 面板端口"].get().strip()))
            remote = Remote(profile)
        except (ValueError, TypeError) as exc:
            self.setup_status.configure(text=str(exc), fg=RED)
            return
        self.setup_button.configure(state="disabled")
        def scanned(key: HostKey):
            self.events.put(("hostkey", remote, key))
        self.task("正在读取服务器 SSH 指纹…", remote.scan_host_key, scanned,
                  self.setup_error)

    def connect_local(self, path_entry: tk.Entry, port_entry: tk.Entry) -> None:
        if self.busy:
            self.setup_status.configure(text="请等待当前操作完成后重试", fg=AMBER)
            return
        try:
            profile = LocalProfile(path_entry.get().strip(), int(port_entry.get().strip()))
            local = Local(profile)
        except (ValueError, TypeError) as exc:
            self.setup_status.configure(text=str(exc), fg=RED)
            return
        self.setup_button.configure(state="disabled")
        self.setup_status.configure(text="正在准备本机 Docker 与 AstrBot…", fg=MUTED)
        def install():
            ensure_docker_ready(lambda message: self.events.put(("docker_progress", message)))
            local.preflight()
            self.events.put(("docker_progress", "Docker 已就绪，正在部署本机 AstrBot…"))
            local.deploy()
            return local
        self.task("正在部署本机 AstrBot…", install,
                  lambda connected: self._activate_backend(connected, "本机 Docker 已连接"),
                  self.setup_error)

    def _activate_backend(self, connected: Remote | Local | NativeLocal, message: str) -> None:
        save_profile(connected.profile)
        if self.dashboard_process and self.dashboard_process.poll() is None:
            self.dashboard_process.terminate()
        self.dashboard_process = None
        self.profile = connected.profile
        self.remote = connected
        self.clear_qr()
        self.apply_accounts([])
        if self.project_window and self.project_window.winfo_exists():
            self.project_window.destroy()
            self.project_window = None
        if self.setup_dialog and self.setup_dialog.winfo_exists():
            self.setup_dialog.destroy()
        self.setup_dialog = None
        self.say(message)
        self.refresh()

    def setup_error(self, error: Exception) -> None:
        if self.setup_dialog and self.setup_dialog.winfo_exists():
            self.setup_status.configure(text=str(error), fg=RED)
            self.setup_button.configure(state="normal")

    def confirm_host_key(self, remote: Remote, key: HostKey) -> None:
        if not self.setup_dialog or not self.setup_dialog.winfo_exists():
            return
        if not remote.is_trusted(key):
            accepted = messagebox.askyesno(
                "核对服务器指纹",
                f"服务器：{remote.profile.host}:{remote.profile.port}\n"
                f"算法：{key.algorithm}\n指纹：{key.fingerprint}\n\n"
                "请与服务器控制台或其他可信渠道显示的指纹核对。确认完全一致后选择“是”。",
                parent=self.setup_dialog)
            if not accepted:
                self.setup_status.configure(text="尚未信任服务器；未进行连接。", fg=AMBER)
                self.setup_button.configure(state="normal")
                return
            remote.trust_host_key(key)
        self.setup_status.configure(text="正在检查 Docker 并部署管理脚本…", fg=MUTED)

        def install():
            remote.preflight()
            remote.deploy()
            remote.call("preflight")
            return remote

        def done(connected: Remote):
            self._activate_backend(connected, "服务器连接已配置")
        self.task("正在部署到你的服务器…", install, done, self.setup_error)

    def open_project(self, settings: bool = False) -> None:
        if self.demo:
            messagebox.showinfo("演示模式", "项目运维需要连接 Docker 或服务器。", parent=self.root)
            return
        if not self.remote or not self.profile or not self.profile.project_path:
            self.say("请先在连接设置中填写 AstrBot 项目目录")
            self.open_setup()
            return
        if self.project_window and self.project_window.winfo_exists():
            self.project_window.lift()
            if settings:
                self.project_window.select_bot_settings()
            return
        self.project_window = ProjectWindow(self.root, self.remote, self.open_dashboard,
                                            resource_path("bot-assistant.ico"),
                                            self.open_native_settings if isinstance(self.remote, NativeLocal) else None)
        if settings:
            self.project_window.select_bot_settings()

    def open_dashboard(self, page: str = "") -> None:
        if not self.remote:
            self.open_setup()
            return
        fragment = {"": "", "providers": "#/providers", "config": "#/config",
                    "persona": "#/persona", "platforms": "#/platforms"}.get(page)
        if fragment is None:
            return
        if isinstance(self.remote, (Local, NativeLocal)):
            webbrowser.open(f"http://127.0.0.1:{self.profile.dashboard_port}/{fragment}")
            self.say("已打开本机 AstrBot 控制台")
            return
        if self.dashboard_process and self.dashboard_process.poll() is None:
            webbrowser.open(f"http://127.0.0.1:{self.dashboard_local_port}/{fragment}")
            return
        def done(result):
            self.dashboard_process, self.dashboard_local_port = result
            webbrowser.open(f"http://127.0.0.1:{self.dashboard_local_port}/{fragment}")
            self.say("AstrBot 控制台已通过本机 SSH 隧道打开")
        self.task("正在建立 AstrBot 控制台安全隧道…",
                  self.remote.dashboard_tunnel, done)

    def show_local_connection(self, account: dict) -> None:
        port = account.get("astrbot_ws_port")
        if not isinstance(self.remote, Local) or not isinstance(port, int):
            return
        messagebox.showinfo(
            "对接 AstrBot",
            f"{account['name']}（{account['qq']}）的 NapCat 已预置反向 WebSocket。\n\n"
            "在 AstrBot 控制台：平台 → 添加平台 → OneBot v11\n"
            "反向 WebSocket 主机：0.0.0.0\n"
            f"反向 WebSocket 端口：{port}\n"
            "令牌：留空\n\n"
            "保存后等待连接，并在 AstrBot 日志中确认适配器已连接。",
            parent=self.root)

    def close(self) -> None:
        if self.dashboard_process and self.dashboard_process.poll() is None:
            self.dashboard_process.terminate()
        self.root.destroy()

    def open_add(self) -> None:
        if self.busy:
            self.say("请等待当前操作完成后再添加账号")
            return
        if self.demo:
            messagebox.showinfo("演示模式", "演示模式不会添加真实账号。", parent=self.root)
            return
        if not self.remote:
            self.open_setup()
            return
        dialog = tk.Toplevel(self.root, bg=BG)
        dialog.title("添加 QQ 账号")
        center_dialog(dialog, self.root, 450, 345)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(dialog, text="添加一个 QQ 账号", bg=BG, fg=TEXT,
                 font=(FONT, 17, "bold")).pack(anchor="w", padx=28, pady=(25, 5))
        tk.Label(dialog, text=("每个账号使用独立的 NapCat 进程与登录数据。"
                               if isinstance(self.remote, NativeLocal) else
                               "每个账号会使用独立的容器与登录数据。"),
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=29)
        tk.Label(dialog, text="QQ 号码", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=29, pady=(18, 3))
        qq = tk.Entry(dialog, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                      relief="flat", font=(LATIN, 12))
        qq.pack(fill="x", padx=29, pady=(0, 8), ipady=7)
        qq.focus_set()
        tk.Label(dialog, text="显示昵称", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=29, pady=(0, 3))
        name = tk.Entry(dialog, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                        relief="flat", font=(FONT, 12))
        name.pack(fill="x", padx=29, pady=(0, 12), ipady=7)
        note = tk.Label(dialog, text="添加后请在手机 QQ 扫码确认登录。",
                        bg=BG, fg=FAINT, font=(FONT, 9))
        note.pack(anchor="w", padx=29)

        def submit():
            number, nickname = qq.get().strip(), name.get().strip()
            if not number.isdecimal() or not (5 <= len(number) <= 12):
                note.configure(text="请输入 5 至 12 位 QQ 号码", fg=RED)
                return
            if not nickname or len(nickname) > 30:
                note.configure(text="请输入 1 至 30 字的显示昵称", fg=RED)
                return
            button.configure(state="disabled")
            note.configure(text=("正在启动独立 NapCat…" if isinstance(self.remote, NativeLocal)
                                 else "正在拉取镜像并启动独立容器，可能需要几分钟…"), fg=MUTED)
            def done(_result):
                if dialog.winfo_exists():
                    dialog.destroy()
                self.refresh_after_add(number)
            self.task(f"正在添加 {nickname}…",
                      lambda: self.remote.call("add", {"qq": number, "name": nickname},
                                               timeout=230), done,
                      lambda error: (button.configure(state="normal"),
                                     note.configure(text=str(error), fg=RED))
                      if dialog.winfo_exists() else None)
        button = tk.Button(dialog, text="添加并开始登录", command=submit,
                           bg=AMBER, fg=BG, bd=0, padx=19, pady=7,
                           font=(FONT, 10, "bold"), cursor="hand2")
        button.pack(anchor="e", padx=29, pady=(13, 0))

    def refresh_after_add(self, qq: str) -> None:
        def done(result):
            self.apply_accounts(result["accounts"])
            self.prepare(qq, force=True)
        self.task("账号已添加，正在读取状态…",
                  lambda: self.remote.call("list"), done)

    def rename_account(self, account: dict) -> None:
        if self.busy:
            self.say("请等待当前操作完成后再修改昵称")
            return
        name = simpledialog.askstring("修改昵称", "显示昵称", initialvalue=account["name"],
                                      parent=self.root)
        if name is None:
            return
        def done(_result):
            self.refresh()
        self.task("正在保存昵称…", lambda: self.remote.call(
            "rename", {"qq": account["qq"], "name": name}), done)

    def remove_account(self, account: dict) -> None:
        if self.busy:
            self.say("请等待当前操作完成后再移除账号")
            return
        if account.get("mode") == "adopted":
            prompt = (f"将 {account['name']}（{account['qq']}）从工作台移除？\n\n"
                      "这是接管的现有账号，原有容器和登录数据会继续运行。")
        else:
            prompt = (f"停止并移除 {account['name']}（{account['qq']}）？\n\n"
                      "QQ 登录数据会保留，重新添加可继续使用。")
        if not messagebox.askyesno(
            "移除账号", prompt, parent=self.root):
            return
        def done(_result):
            if self.qr_target == account["qq"]:
                self.clear_qr()
            self.refresh()
        self.task("正在移除账号…", lambda: self.remote.call(
            "remove", {"qq": account["qq"]}, timeout=90), done)

    def prepare(self, qq: str, force: bool = False) -> None:
        if self.busy:
            self.say("请等待当前操作完成后再获取登录码")
            return
        if self.demo or not self.remote:
            return
        account = next((item for item in self.accounts if item["qq"] == qq), None)
        if not account or (account["state"] == "online" and not force):
            return
        self.qr_target = qq
        self.qr_name = account["name"]
        self.qr_image = None
        self.draw_qr()
        def done(result):
            if result.get("state") == "online":
                self.clear_qr()
                self.say(f"{self.qr_name or account['name']}已经在线")
                self.refresh()
                return
            if result.get("state") != "qr_ready" or result.get("qq") != qq:
                self.say("服务器没有返回匹配的登录码")
                return
            try:
                raw = base64.b64decode(result["qr_png_base64"], validate=True)
                with Image.open(io.BytesIO(raw)) as source:
                    qr = source.convert("RGB").resize((190, 190),
                                                       Image.Resampling.NEAREST)
                self.qr_image = ImageTk.PhotoImage(qr)
                self.qr_age = int(result["age_seconds"])
                self.qr_ttl = int(result["ttl_seconds"])
                self.qr_started = time.monotonic()
                self.draw_qr()
                self.say(f"请用手机 QQ 扫描 {account['name']} 的登录码")
            except (KeyError, ValueError, OSError):
                self.say("登录码图片无法读取，请重试")
        self.task(f"正在为 {account['name']} 获取新登录码…",
                  lambda: self.remote.call("prepare", {"qq": qq}, timeout=125), done,
                  lambda _error: self.clear_qr())

    def qr_remaining(self) -> int:
        if not self.qr_target:
            return 0
        return max(0, self.qr_ttl - self.qr_age -
                   int(time.monotonic() - self.qr_started)) if self.qr_image else 0

    def clear_qr(self) -> None:
        self.qr_target = ""
        self.qr_name = ""
        self.qr_image = None
        self.qr_ttl = 0
        self.draw_qr()

    def draw_qr(self) -> None:
        c = self.qr_panel
        c.delete("all")
        rounded(c, 0, 0, 840, 226, 22, SURFACE)
        rounded(c, 18, 17, 226, 209, 17, WHITE)
        if self.qr_image is not None and self.qr_remaining() > 0:
            c.create_image(122, 113, image=self.qr_image)
        else:
            c.create_oval(81, 55, 163, 137, fill="#E8EFE7", outline="")
            c.create_text(122, 96, text="⌁", fill="#537061",
                          font=(LATIN, 48, "bold"))
            c.create_text(122, 170, text="登录码待获取", fill="#789181",
                          font=(FONT, 10))
        if self.qr_target and self.qr_image and self.qr_remaining() > 0:
            title = f"请登录 {self.qr_name}"
            description = "打开手机 QQ 扫描左侧登录码，\n并在手机上确认本次登录。"
            detail = f"登录码剩余约 {self.qr_remaining()} 秒"
        elif self.qr_target and self.qr_image:
            title = f"{self.qr_name}的登录码已过期"
            description = "旧登录码已隐藏。请点击账号卡片，\n重新获取这一账号的新码。"
            detail = "过期码不会继续显示"
        elif self.qr_target:
            title = f"正在准备 {self.qr_name} 的登录码"
            description = "仅处理所选账号，\n请稍候。"
            detail = "其他账号不会重启"
        else:
            title = "所有账号，一目了然"
            description = "账号掉线时，点击对应卡片获取登录码。\n手机 QQ 扫码确认后，状态会自动更新。"
            detail = "每个账号独立运行"
        c.create_text(253, 27, text="LOGIN  /  RECOVERY", fill=AMBER,
                      anchor="nw", font=(LATIN, 9, "bold"))
        c.create_text(253, 57, text=title, fill=TEXT, anchor="nw",
                      font=(FONT, 16, "bold"))
        c.create_text(253, 104, text=description, fill=MUTED,
                      anchor="nw", font=(FONT, 10))
        c.create_text(253, 181, text=detail, fill=JADE,
                      anchor="nw", font=(FONT, 9, "bold"))


def main() -> int:
    if "--self-test" in sys.argv:
        try:
            output = Path(sys.argv[sys.argv.index("--self-test") + 1])
            manager = bundled_manager_path()
            thinking_source, thinking_metadata = bundled_thinking_plugin()
            local = Local(LocalProfile(str(config_home() / "selftest-project")))
            native = NativeLocal(NativeProfile(str(config_home() / "selftest-native")))
            Image.new("RGB", (1, 1))
            output.write_text(json.dumps({"ok": manager.is_file() and
                                          "astrbot_plugin_thinking_control" in thinking_source and
                                          "name: astrbot_plugin_thinking_control" in thinking_metadata,
                                          "app_version": __version__,
                                          "manager_bytes": manager.stat().st_size,
                                          "local_manager": local.module.VERSION,
                                          "native_manager": isinstance(native, NativeLocal)}),
                              encoding="utf-8")
            return 0 if manager.is_file() else 1
        except Exception as exc:
            try:
                output.write_text(json.dumps({"ok": False, "error": repr(exc)}),
                                  encoding="utf-8")
            except Exception:
                pass
            return 1
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root, demo="--demo" in sys.argv)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
