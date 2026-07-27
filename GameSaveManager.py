"""
Game Save Manager
------------------
A desktop app to let multiple Windows users share one PC and one game library,
while keeping completely separate save progress per person, per game.

Works with any game where a launcher/exe starts it and saves live in a folder
(e.g. FitGirl repacks, Documents\\My Games saves, AppData saves, etc.)

Requires only Python 3 (Tkinter ships built-in). No extra installs needed.
"""

import os
import sys
import json
import time
import shutil
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

DEFAULT_CONFIG = {"users": {}, "games": {}}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def hash_password(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core save-swapping logic (equivalent to what the .bat file did)
# ---------------------------------------------------------------------------

def mirror_copy(src, dst):
    """Make dst an exact copy of src (like robocopy /MIR).
    If src doesn't exist yet, just ensure dst exists (empty)."""
    os.makedirs(os.path.dirname(dst) if os.path.dirname(dst) else ".", exist_ok=True)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    if os.path.exists(src):
        shutil.copytree(src, dst)
    else:
        os.makedirs(dst, exist_ok=True)


def is_process_running(process_name):
    """Reliable check using tasklist, parsed as CSV to avoid name truncation."""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        result = subprocess.run(
            ["tasklist", "/fi", f"imagename eq {process_name}.exe", "/fo", "csv", "/nh"],
            capture_output=True, text=True, creationflags=creationflags
        )
        return process_name.lower() in result.stdout.lower()
    except Exception:
        return False


def get_game_dir(launcher_exe):
    return os.path.dirname(launcher_exe)


def get_profile_folder(game_name, user_name, launcher_exe):
    game_dir = get_game_dir(launcher_exe)
    return os.path.join(game_dir, "Profiles", f"{game_name}_{user_name}")


def get_backup_folder(game_name, launcher_exe):
    game_dir = get_game_dir(launcher_exe)
    return os.path.join(game_dir, "Profiles", "_LastActive_Backup", game_name)


def play_game(game_name, game_info, user_name, log, on_done):
    """Runs in a background thread. log(msg) updates the UI. on_done() called at the end."""
    try:
        save_folder = game_info["save_folder"]
        launcher_exe = game_info["launcher_exe"]
        process_name = (game_info.get("process_name") or "").strip()

        profile_folder = get_profile_folder(game_name, user_name, launcher_exe)
        backup_folder = get_backup_folder(game_name, launcher_exe)

        log("Backing up current live save (safety net)...")
        mirror_copy(save_folder, backup_folder)

        log(f"Loading save data for {user_name}...")
        mirror_copy(profile_folder, save_folder)

        log(f"Starting {game_name} for {user_name}...")
        proc = subprocess.Popen([launcher_exe], cwd=get_game_dir(launcher_exe))

        if process_name:
            log("Waiting for the game to actually start...")
            while not is_process_running(process_name):
                time.sleep(2)
            log("Game is running. This will continue once you quit the game.")
            while is_process_running(process_name):
                time.sleep(3)
        else:
            proc.wait()

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
        self.geometry("560x480")
        self.resizable(False, False)

        self.cfg = load_config()
        self.current_user = None

        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)

        self.show_login()

    # ---------------- Navigation ----------------

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
        self.pack(fill="both", expand=True, padx=30, pady=30)

        ttk.Label(self, text="Game Save Manager", font=("Segoe UI", 18, "bold")).pack(pady=(0, 20))

        users = list(app.cfg["users"].keys())

        if not users:
            ttk.Label(self, text="No users yet. Add one to get started.").pack(pady=10)
            ttk.Button(self, text="Add User", command=self.add_user).pack()
            return

        ttk.Label(self, text="Select user:").pack(anchor="w")
        self.user_var = tk.StringVar(value=users[0])
        combo = ttk.Combobox(self, textvariable=self.user_var, values=users, state="readonly")
        combo.pack(fill="x", pady=(0, 15))

        ttk.Label(self, text="Password:").pack(anchor="w")
        self.pw_var = tk.StringVar()
        pw_entry = ttk.Entry(self, textvariable=self.pw_var, show="*")
        pw_entry.pack(fill="x", pady=(0, 15))
        pw_entry.bind("<Return>", lambda e: self.login())

        ttk.Button(self, text="Login", command=self.login).pack(fill="x", pady=(0, 10))
        ttk.Button(self, text="Add User", command=self.add_user).pack(fill="x")

    def login(self):
        user = self.user_var.get()
        pw = self.pw_var.get()
        expected = self.app.cfg["users"].get(user, {}).get("password_hash")
        if expected and hash_password(pw) == expected:
            self.app.current_user = user
            self.app.show_library()
        else:
            messagebox.showerror("Login failed", "Incorrect password.")

    def add_user(self):
        AddUserDialog(self.app)


class AddUserDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Add User")
        self.geometry("300x200")
        self.resizable(False, False)
        self.grab_set()

        ttk.Label(self, text="Name:").pack(anchor="w", padx=15, pady=(15, 0))
        self.name_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.name_var).pack(fill="x", padx=15)

        ttk.Label(self, text="Password:").pack(anchor="w", padx=15, pady=(10, 0))
        self.pw_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.pw_var, show="*").pack(fill="x", padx=15)

        ttk.Button(self, text="Save", command=self.save).pack(pady=20)

    def save(self):
        name = self.name_var.get().strip()
        pw = self.pw_var.get()
        if not name or not pw:
            messagebox.showerror("Missing info", "Name and password are required.")
            return
        if name in self.app.cfg["users"]:
            messagebox.showerror("Exists", "A user with that name already exists.")
            return
        self.app.cfg["users"][name] = {"password_hash": hash_password(pw)}
        save_config(self.app.cfg)
        self.destroy()
        self.app.show_login()


class LibraryFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.pack(fill="both", expand=True, padx=20, pady=20)

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text=f"Signed in as {app.current_user}", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(top, text="Log out", command=app.show_login).pack(side="right")

        ttk.Separator(self).pack(fill="x", pady=10)

        self.tree = ttk.Treeview(self, columns=("game",), show="headings", height=12)
        self.tree.heading("game", text="Your Games")
        self.tree.pack(fill="both", expand=True)
        self.refresh_list()

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=10)
        ttk.Button(btns, text="Play", command=self.play_selected).pack(side="left")
        ttk.Button(btns, text="Add Game", command=self.add_game).pack(side="left", padx=5)
        ttk.Button(btns, text="Edit", command=self.edit_game).pack(side="left", padx=5)
        ttk.Button(btns, text="Remove", command=self.remove_game).pack(side="left", padx=5)

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, foreground="#333").pack(fill="x", pady=(10, 0))

        self.log_box = tk.Text(self, height=6, state="disabled", bg="#f4f4f4")
        self.log_box.pack(fill="x", pady=(5, 0))

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
        self.status_var.set(f"Playing {name}...")

        def worker():
            play_game(name, game_info, self.app.current_user, self.log, on_done)

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
        self.geometry("480x320")
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
        ttk.Button(row1, text="Browse", command=self.browse_save).pack(side="left", padx=(5, 0))

        ttk.Label(self, text="Launcher / game exe:").pack(anchor="w", padx=15, pady=(10, 0))
        row2 = ttk.Frame(self)
        row2.pack(fill="x", padx=15)
        self.exe_var = tk.StringVar(value=info.get("launcher_exe", ""))
        ttk.Entry(row2, textvariable=self.exe_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="Browse", command=self.browse_exe).pack(side="left", padx=(5, 0))

        ttk.Label(self, text="Real game process name (optional - leave blank if launcher\nstays open the whole time you play):").pack(anchor="w", padx=15, pady=(10, 0))
        self.proc_var = tk.StringVar(value=info.get("process_name", ""))
        ttk.Entry(self, textvariable=self.proc_var).pack(fill="x", padx=15)

        ttk.Button(self, text="Save", command=self.save).pack(pady=20)

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
