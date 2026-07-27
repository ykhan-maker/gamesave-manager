"""
Game Save Manager
------------------
A desktop app to let multiple Windows users share one PC and one game library,
while keeping completely separate save progress per person, per game.

Roles:
  - Admin (default: Yawar / 16102001): can add/edit/remove games, add users,
    and change settings.
  - Standard User (default: Neha / neha): can only see the game library and play.

Games are launched with admin permission (Windows will show a UAC prompt each
time - click Yes. This is normal Windows security behavior, not something this
app can silently skip).

Requires only Python 3 (Tkinter ships built-in). No extra installs needed.
"""

import os
import io
import csv
import sys
import json
import time
import shutil
import ctypes
import hashlib
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---------------------------------------------------------------------------
# Config storage - lives in %APPDATA%\GameSaveManager\config.json so it
# persists no matter where you move this app.
# ---------------------------------------------------------------------------

APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
CONFIG_DIR = os.path.join(APPDATA, "GameSaveManager")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def hash_password(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def default_config():
    return {
        "users": {
            "Yawar": {"password_hash": hash_password("16102001"), "role": "admin"},
            "Neha": {"password_hash": hash_password("neha"), "role": "user"},
        },
        "games": {},
        "settings": {
            "launch_as_admin": True,
        },
    }


def load_config():
    if not os.path.exists(CONFIG_FILE):
        cfg = default_config()
        save_config(cfg)
        return cfg
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # Fill in anything missing from older config versions
    cfg.setdefault("users", {})
    cfg.setdefault("games", {})
    cfg.setdefault("settings", {})
    cfg["settings"].setdefault("launch_as_admin", True)
    for u in cfg["users"].values():
        u.setdefault("role", "user")
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

BG = "#1e1f29"
BG_CARD = "#262838"
BG_INPUT = "#2d2f42"
FG = "#e8e8ef"
FG_SUBTLE = "#9aa0b4"
ACCENT = "#6c5ce7"
ACCENT_HOVER = "#8177ea"
DANGER = "#e74c3c"
DANGER_HOVER = "#ff6b5b"

FONT_TITLE = ("Segoe UI", 20, "bold")
FONT_SUBTITLE = ("Segoe UI", 11)
FONT_LABEL = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")


def apply_theme(root):
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=BG_CARD)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT_LABEL)
    style.configure("Title.TLabel", background=BG, foreground=FG, font=FONT_TITLE)
    style.configure("Subtitle.TLabel", background=BG, foreground=FG_SUBTLE, font=FONT_SUBTITLE)
    style.configure("Card.TLabel", background=BG_CARD, foreground=FG, font=FONT_LABEL)

    style.configure("TButton", background=ACCENT, foreground="white",
                     font=FONT_BOLD, padding=8, borderwidth=0)
    style.map("TButton", background=[("active", ACCENT_HOVER)])

    style.configure("Danger.TButton", background=DANGER, foreground="white",
                     font=FONT_BOLD, padding=8, borderwidth=0)
    style.map("Danger.TButton", background=[("active", DANGER_HOVER)])

    style.configure("Secondary.TButton", background=BG_CARD, foreground=FG,
                     font=FONT_LABEL, padding=8, borderwidth=0)
    style.map("Secondary.TButton", background=[("active", "#33354a")])

    style.configure("TEntry", fieldbackground=BG_INPUT, foreground=FG,
                     insertcolor=FG, borderwidth=0, padding=6)
    style.configure("TCombobox", fieldbackground=BG_INPUT, foreground=FG,
                     background=BG_INPUT, arrowcolor=FG)

    style.configure("Treeview", background=BG_CARD, fieldbackground=BG_CARD,
                     foreground=FG, rowheight=30, borderwidth=0, font=FONT_LABEL)
    style.configure("Treeview.Heading", background=ACCENT, foreground="white",
                     font=FONT_BOLD, borderwidth=0)
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])

    style.configure("TSeparator", background="#3a3c52")
    style.configure("TCheckbutton", background=BG, foreground=FG, font=FONT_LABEL)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("Card.TCheckbutton", background=BG_CARD, foreground=FG, font=FONT_LABEL)
    style.map("Card.TCheckbutton", background=[("active", BG_CARD)])

    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=BG_CARD, foreground=FG,
                     font=FONT_BOLD, padding=(16, 8))
    style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "white")])


# ---------------------------------------------------------------------------
# Core save-swapping + process logic
# ---------------------------------------------------------------------------

def mirror_copy(src, dst):
    """Make dst an exact copy of src (like robocopy /MIR)."""
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    if os.path.exists(src):
        shutil.copytree(src, dst)
    else:
        os.makedirs(dst, exist_ok=True)


def _creationflags():
    return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def is_process_running(process_name):
    """Exact-match check via tasklist's own filter (avoids display truncation issues)."""
    try:
        result = subprocess.run(
            ["tasklist", "/fi", f"imagename eq {process_name}.exe", "/fo", "csv", "/nh"],
            capture_output=True, text=True, creationflags=_creationflags()
        )
        return process_name.lower() in result.stdout.lower()
    except Exception:
        return False


def list_all_processes(keyword):
    """Returns [(name_without_exe, pid), ...] for processes matching keyword."""
    matches = []
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, creationflags=_creationflags()
        )
        reader = csv.reader(io.StringIO(result.stdout))
        seen = set()
        for row in reader:
            if not row:
                continue
            name = row[0]
            pid = row[1] if len(row) > 1 else ""
            if keyword.lower() in name.lower():
                base = name[:-4] if name.lower().endswith(".exe") else name
                key = base.lower()
                if key not in seen:
                    seen.add(key)
                    matches.append((base, pid))
    except Exception:
        pass
    return matches


def launch_as_admin(exe_path, cwd):
    """Launch exe_path elevated via Windows UAC (ShellExecute 'runas').
    This WILL show a UAC prompt - that's Windows security, not something
    this app can bypass silently."""
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe_path, None, cwd, 1)


def launch_normal(exe_path, cwd):
    subprocess.Popen([exe_path], cwd=cwd)


def get_game_dir(launcher_exe):
    return os.path.dirname(launcher_exe)


def get_profile_folder(game_name, user_name, launcher_exe):
    return os.path.join(get_game_dir(launcher_exe), "Profiles", f"{game_name}_{user_name}")


def get_backup_folder(game_name, launcher_exe):
    return os.path.join(get_game_dir(launcher_exe), "Profiles", "_LastActive_Backup", game_name)


def resolve_process_name(game_info):
    explicit = (game_info.get("process_name") or "").strip()
    if explicit:
        return explicit
    return os.path.splitext(os.path.basename(game_info["launcher_exe"]))[0]


def play_game(game_name, game_info, user_name, launch_as_admin_flag, log, on_done):
    """Runs in a background thread."""
    try:
        save_folder = game_info["save_folder"]
        launcher_exe = game_info["launcher_exe"]
        process_name = resolve_process_name(game_info)

        profile_folder = get_profile_folder(game_name, user_name, launcher_exe)
        backup_folder = get_backup_folder(game_name, launcher_exe)

        log("Backing up current live save (safety net)...")
        mirror_copy(save_folder, backup_folder)

        log(f"Loading save data for {user_name}...")
        mirror_copy(profile_folder, save_folder)

        if launch_as_admin_flag:
            log(f"Starting {game_name} for {user_name} (requesting admin permission - approve the Windows prompt)...")
            launch_as_admin(launcher_exe, get_game_dir(launcher_exe))
        else:
            log(f"Starting {game_name} for {user_name}...")
            launch_normal(launcher_exe, get_game_dir(launcher_exe))

        log("Waiting for the game to actually start...")
        waited = 0
        warned = False
        while not is_process_running(process_name):
            time.sleep(2)
            waited += 2
            if waited > 40 and not warned:
                warned = True
                log(f"Still waiting... if the game is already running, the process "
                    f"name '{process_name}' may be wrong. Check with Find Process Name.")

        log("Game is running. This will continue once you quit the game.")
        while is_process_running(process_name):
            time.sleep(3)

        log("Game closed. Saving progress back to profile...")
        mirror_copy(save_folder, profile_folder)
        log(f"Done. Progress saved for {user_name}.")
    except Exception as e:
        log(f"ERROR: {e}")
    finally:
        on_done()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Game Save Manager")
        self.geometry("640x560")
        self.minsize(640, 560)
        apply_theme(self)

        self.cfg = load_config()
        self.current_user = None

        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)

        self.show_login()

    def is_admin_user(self):
        if not self.current_user:
            return False
        return self.cfg["users"].get(self.current_user, {}).get("role") == "admin"

    def clear(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    def show_login(self):
        self.current_user = None
        self.clear()
        LoginFrame(self.container, self)

    def show_library(self):
        self.clear()
        LibraryFrame(self.container, self)


class LoginFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.pack(fill="both", expand=True, padx=40, pady=40)

        ttk.Label(self, text="Game Save Manager", style="Title.TLabel").pack(pady=(10, 4))
        ttk.Label(self, text="Sign in to load your own saves", style="Subtitle.TLabel").pack(pady=(0, 30))

        users = list(app.cfg["users"].keys())

        if not users:
            ttk.Label(self, text="No users yet. Add the first (admin) account to get started.").pack(pady=10)
            ttk.Button(self, text="Add First User", command=lambda: AddUserDialog(app, force_admin=True)).pack()
            return

        card = ttk.Frame(self, style="Card.TFrame", padding=25)
        card.pack(fill="x")

        ttk.Label(card, text="User", style="Card.TLabel").pack(anchor="w")
        self.user_var = tk.StringVar(value=users[0])
        combo = ttk.Combobox(card, textvariable=self.user_var, values=users, state="readonly")
        combo.pack(fill="x", pady=(4, 16))

        ttk.Label(card, text="Password", style="Card.TLabel").pack(anchor="w")
        self.pw_var = tk.StringVar()
        pw_entry = ttk.Entry(card, textvariable=self.pw_var, show="*")
        pw_entry.pack(fill="x", pady=(4, 20))
        pw_entry.bind("<Return>", lambda e: self.login())

        ttk.Button(card, text="Login", command=self.login).pack(fill="x")

    def login(self):
        user = self.user_var.get()
        pw = self.pw_var.get()
        expected = self.app.cfg["users"].get(user, {}).get("password_hash")
        if expected and hash_password(pw) == expected:
            self.app.current_user = user
            self.app.show_library()
        else:
            messagebox.showerror("Login failed", "Incorrect password.")


class AddUserDialog(tk.Toplevel):
    def __init__(self, app, force_admin=False):
        super().__init__(app)
        self.app = app
        self.force_admin = force_admin
        self.title("Add User")
        self.geometry("320x320")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()

        ttk.Label(self, text="Name:").pack(anchor="w", padx=15, pady=(15, 0))
        self.name_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.name_var).pack(fill="x", padx=15)

        ttk.Label(self, text="Password:").pack(anchor="w", padx=15, pady=(10, 0))
        self.pw_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.pw_var, show="*").pack(fill="x", padx=15)

        ttk.Label(self, text="Role:").pack(anchor="w", padx=15, pady=(10, 0))
        self.role_var = tk.StringVar(value="Admin" if force_admin else "Standard User")
        role_combo = ttk.Combobox(self, textvariable=self.role_var,
                                   values=["Admin", "Standard User"], state="readonly")
        role_combo.pack(fill="x", padx=15)
        if force_admin:
            role_combo.configure(state="disabled")

        ttk.Button(self, text="Save", command=self.save).pack(pady=25)

    def save(self):
        name = self.name_var.get().strip()
        pw = self.pw_var.get()
        role = "admin" if self.role_var.get() == "Admin" else "user"
        if not name or not pw:
            messagebox.showerror("Missing info", "Name and password are required.")
            return
        if name in self.app.cfg["users"]:
            messagebox.showerror("Exists", "A user with that name already exists.")
            return
        self.app.cfg["users"][name] = {"password_hash": hash_password(pw), "role": role}
        save_config(self.app.cfg)
        self.destroy()
        self.app.show_login()


class ProcessFinderDialog(tk.Toplevel):
    """Lets admin search running processes by keyword, then copy or auto-fill the exact name."""
    def __init__(self, app, target_var=None):
        super().__init__(app)
        self.app = app
        self.target_var = target_var
        self.title("Find Exact Process Name")
        self.geometry("460x420")
        self.configure(bg=BG)
        self.grab_set()

        ttk.Label(self, text="Start the game yourself first, get into actual gameplay,\n"
                              "then type a short keyword below (e.g. 'mafia') and search.",
                  style="Subtitle.TLabel").pack(padx=15, pady=(15, 10), anchor="w")

        row = ttk.Frame(self)
        row.pack(fill="x", padx=15)
        self.keyword_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.keyword_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.search())
        ttk.Button(row, text="Search", command=self.search).pack(side="left", padx=(5, 0))

        self.tree = ttk.Treeview(self, columns=("name", "pid"), show="headings", height=10)
        self.tree.heading("name", text="Process Name")
        self.tree.heading("pid", text="PID")
        self.tree.column("pid", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=15, pady=15)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=15, pady=(0, 15))
        ttk.Button(btns, text="Copy", command=self.copy_selected, style="Secondary.TButton").pack(side="left")
        if target_var is not None:
            ttk.Button(btns, text="Use This", command=self.use_selected).pack(side="left", padx=5)

    def search(self):
        keyword = self.keyword_var.get().strip()
        if not keyword:
            return
        self.tree.delete(*self.tree.get_children())
        results = list_all_processes(keyword)
        if not results:
            messagebox.showinfo("No matches", "No running processes matched that keyword. "
                                                "Make sure the game is actually running right now.")
            return
        for name, pid in results:
            self.tree.insert("", "end", values=(name, pid))

    def _get_selected_name(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a result", "Please select a process from the list first.")
            return None
        return self.tree.item(sel[0], "values")[0]

    def copy_selected(self):
        name = self._get_selected_name()
        if name is None:
            return
        self.clipboard_clear()
        self.clipboard_append(name)
        self.update()
        messagebox.showinfo("Copied", f"'{name}' copied to clipboard.")

    def use_selected(self):
        name = self._get_selected_name()
        if name is None:
            return
        self.target_var.set(name)
        self.destroy()


class AdminPanel(tk.Toplevel):
    """Manage users and app-wide settings. Admin only."""
    def __init__(self, app, on_change=None):
        super().__init__(app)
        self.app = app
        self.on_change = on_change
        self.title("Admin Panel")
        self.geometry("440x420")
        self.configure(bg=BG)
        self.grab_set()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=15, pady=15)

        users_tab = ttk.Frame(notebook)
        settings_tab = ttk.Frame(notebook)
        notebook.add(users_tab, text="Users")
        notebook.add(settings_tab, text="Settings")

        # --- Users tab ---
        self.user_tree = ttk.Treeview(users_tab, columns=("name", "role"), show="headings", height=10)
        self.user_tree.heading("name", text="Name")
        self.user_tree.heading("role", text="Role")
        self.user_tree.pack(fill="both", expand=True, pady=(10, 10))
        self.refresh_users()

        u_btns = ttk.Frame(users_tab)
        u_btns.pack(fill="x")
        ttk.Button(u_btns, text="Add User", command=lambda: AddUserDialog(self.app)).pack(side="left")
        ttk.Button(u_btns, text="Reset Password", command=self.reset_password).pack(side="left", padx=5)
        ttk.Button(u_btns, text="Remove", style="Danger.TButton", command=self.remove_user).pack(side="left")

        # --- Settings tab ---
        self.admin_launch_var = tk.BooleanVar(value=self.app.cfg["settings"].get("launch_as_admin", True))
        ttk.Checkbutton(
            settings_tab,
            text="Launch all games with admin permission (Windows will ask for confirmation each time)",
            variable=self.admin_launch_var,
            command=self.toggle_admin_launch,
            wraplength=380,
        ).pack(anchor="w", pady=20, padx=10)

        ttk.Label(settings_tab, text=f"Config file location:\n{CONFIG_FILE}",
                  style="Subtitle.TLabel", wraplength=380).pack(anchor="w", padx=10, pady=10)

    def refresh_users(self):
        self.user_tree.delete(*self.user_tree.get_children())
        for name, info in self.app.cfg["users"].items():
            role = "Admin" if info.get("role") == "admin" else "Standard User"
            self.user_tree.insert("", "end", iid=name, values=(name, role))

    def get_selected_user(self):
        sel = self.user_tree.selection()
        return sel[0] if sel else None

    def reset_password(self):
        name = self.get_selected_user()
        if not name:
            messagebox.showinfo("Select a user", "Please select a user first.")
            return
        dialog = tk.Toplevel(self)
        dialog.title(f"Reset password for {name}")
        dialog.geometry("300x150")
        dialog.configure(bg=BG)
        dialog.grab_set()
        ttk.Label(dialog, text="New password:").pack(anchor="w", padx=15, pady=(15, 0))
        pw_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=pw_var, show="*").pack(fill="x", padx=15)

        def save():
            pw = pw_var.get()
            if not pw:
                messagebox.showerror("Missing password", "Enter a new password.")
                return
            self.app.cfg["users"][name]["password_hash"] = hash_password(pw)
            save_config(self.app.cfg)
            dialog.destroy()

        ttk.Button(dialog, text="Save", command=save).pack(pady=20)

    def remove_user(self):
        name = self.get_selected_user()
        if not name:
            messagebox.showinfo("Select a user", "Please select a user first.")
            return
        if name == self.app.current_user:
            messagebox.showerror("Not allowed", "You can't remove the account you're currently logged in as.")
            return
        admins_left = sum(1 for u in self.app.cfg["users"].values() if u.get("role") == "admin")
        if self.app.cfg["users"][name].get("role") == "admin" and admins_left <= 1:
            messagebox.showerror("Not allowed", "At least one admin account must remain.")
            return
        if messagebox.askyesno("Remove user", f"Remove '{name}'?\n(Their save profiles are not deleted.)"):
            del self.app.cfg["users"][name]
            save_config(self.app.cfg)
            self.refresh_users()

    def toggle_admin_launch(self):
        self.app.cfg["settings"]["launch_as_admin"] = bool(self.admin_launch_var.get())
        save_config(self.app.cfg)


class LibraryFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.pack(fill="both", expand=True, padx=25, pady=25)
        is_admin = app.is_admin_user()

        top = ttk.Frame(self)
        top.pack(fill="x")
        role_label = "Admin" if is_admin else "Player"
        ttk.Label(top, text=f"{app.current_user}", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text=f"  ({role_label})", style="Subtitle.TLabel").pack(side="left")
        ttk.Button(top, text="Log out", style="Secondary.TButton", command=app.show_login).pack(side="right")

        ttk.Separator(self).pack(fill="x", pady=15)

        self.tree = ttk.Treeview(self, columns=("game",), show="headings", height=10)
        self.tree.heading("game", text="Game Library")
        self.tree.pack(fill="both", expand=True)
        self.refresh_list()

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=15)
        ttk.Button(btns, text="Play", command=self.play_selected).pack(side="left")

        if is_admin:
            ttk.Button(btns, text="Add Game", style="Secondary.TButton", command=self.add_game).pack(side="left", padx=5)
            ttk.Button(btns, text="Edit", style="Secondary.TButton", command=self.edit_game).pack(side="left", padx=5)
            ttk.Button(btns, text="Remove", style="Danger.TButton", command=self.remove_game).pack(side="left", padx=5)
            ttk.Button(btns, text="Find Process Name", style="Secondary.TButton",
                       command=lambda: ProcessFinderDialog(self.app)).pack(side="right")
            ttk.Button(btns, text="Admin Panel", style="Secondary.TButton",
                       command=lambda: AdminPanel(self.app)).pack(side="right", padx=5)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, style="Subtitle.TLabel").pack(fill="x", pady=(5, 5))

        self.log_box = tk.Text(self, height=7, state="disabled", bg=BG_CARD, fg=FG,
                                insertbackground=FG, borderwidth=0, font=("Consolas", 9))
        self.log_box.pack(fill="x")

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for name in self.app.cfg["games"]:
            self.tree.insert("", "end", iid=name, values=(name,))

    def get_selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def log(self, msg):
        def update():
            self.log_box.config(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, update)

    def play_selected(self):
        name = self.get_selected()
        if not name:
            messagebox.showinfo("Select a game", "Please select a game first.")
            return
        game_info = self.app.cfg["games"][name]
        launch_admin_flag = self.app.cfg["settings"].get("launch_as_admin", True)
        self.status_var.set(f"Playing {name}...")

        def worker():
            play_game(name, game_info, self.app.current_user, launch_admin_flag, self.log, on_done)

        def on_done():
            self.after(0, lambda: self.status_var.set("Ready."))

        threading.Thread(target=worker, daemon=True).start()

    def add_game(self):
        GameDialog(self.app, on_saved=self.refresh_list)

    def edit_game(self):
        name = self.get_selected()
        if not name:
            messagebox.showinfo("Select a game", "Please select a game first.")
            return
        GameDialog(self.app, existing_name=name, on_saved=self.refresh_list)

    def remove_game(self):
        name = self.get_selected()
        if not name:
            messagebox.showinfo("Select a game", "Please select a game first.")
            return
        if messagebox.askyesno("Remove game", f"Remove '{name}' from the library?\n(Save files are not deleted.)"):
            del self.app.cfg["games"][name]
            save_config(self.app.cfg)
            self.refresh_list()


class GameDialog(tk.Toplevel):
    def __init__(self, app, existing_name=None, on_saved=None):
        super().__init__(app)
        self.app = app
        self.existing_name = existing_name
        self.on_saved = on_saved
        self.title("Edit Game" if existing_name else "Add Game")
        self.geometry("500x380")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        info = app.cfg["games"].get(existing_name, {}) if existing_name else {}

        ttk.Label(self, text="Game name:").pack(anchor="w", padx=15, pady=(15, 0))
        self.name_var = tk.StringVar(value=existing_name or "")
        ttk.Entry(self, textvariable=self.name_var).pack(fill="x", padx=15)

        ttk.Label(self, text="Save folder (where the game stores progress):").pack(anchor="w", padx=15, pady=(10, 0))
        row1 = ttk.Frame(self)
        row1.pack(fill="x", padx=15)
        self.save_var = tk.StringVar(value=info.get("save_folder", ""))
        ttk.Entry(row1, textvariable=self.save_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row1, text="Browse", style="Secondary.TButton", command=self.browse_save).pack(side="left", padx=(5, 0))

        ttk.Label(self, text="Launcher / game exe:").pack(anchor="w", padx=15, pady=(10, 0))
        row2 = ttk.Frame(self)
        row2.pack(fill="x", padx=15)
        self.exe_var = tk.StringVar(value=info.get("launcher_exe", ""))
        ttk.Entry(row2, textvariable=self.exe_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="Browse", style="Secondary.TButton", command=self.browse_exe).pack(side="left", padx=(5, 0))

        ttk.Label(self, text="Real game process name (leave blank to guess from exe name):").pack(
            anchor="w", padx=15, pady=(10, 0))
        row3 = ttk.Frame(self)
        row3.pack(fill="x", padx=15)
        self.proc_var = tk.StringVar(value=info.get("process_name", ""))
        ttk.Entry(row3, textvariable=self.proc_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row3, text="Find", style="Secondary.TButton",
                   command=lambda: ProcessFinderDialog(self.app, target_var=self.proc_var)).pack(side="left", padx=(5, 0))

        ttk.Button(self, text="Save", command=self.save).pack(pady=25)

    def browse_save(self):
        path = filedialog.askdirectory(title="Select the save folder")
        if path:
            self.save_var.set(path)

    def browse_exe(self):
        path = filedialog.askopenfilename(title="Select the launcher/game exe", filetypes=[("Executable", "*.exe")])
        if path:
            self.exe_var.set(path)

    def save(self):
        name = self.name_var.get().strip()
        save_folder = self.save_var.get().strip()
        launcher_exe = self.exe_var.get().strip()
        process_name = self.proc_var.get().strip()

        if not name or not save_folder or not launcher_exe:
            messagebox.showerror("Missing info", "Game name, save folder, and launcher exe are all required.")
            return

        if self.existing_name and self.existing_name != name:
            del self.app.cfg["games"][self.existing_name]

        self.app.cfg["games"][name] = {
            "save_folder": save_folder,
            "launcher_exe": launcher_exe,
            "process_name": process_name,
        }
        save_config(self.app.cfg)
        if self.on_saved:
            self.on_saved()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
