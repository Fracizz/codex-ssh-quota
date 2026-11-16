"""Optional Windows title-area companion. Uses Tk/Win32; does not inject into Codex."""
import os
from pathlib import Path
import queue
import sys
import threading
import time

import server

TITLE = "Codex SSH Quota HUD"
DETAIL_URL = "codex://plugins/codex-ssh-quota@codex-ssh-quota/app/codex_quota"


def summary(data, now=None):
    """Small, dated snapshot indicator; expired windows never look current."""
    now = time.time() if now is None else now
    buckets = data.get("buckets") or []
    bucket = next((b for b in buckets if b.get("id") == "codex"), buckets[0] if buckets else {})
    parts = []
    for window in bucket.get("windows", [])[:2]:
        mins = window.get("durationMins")
        label = "周" if mins == 10080 else "5小时" if mins == 300 else (
            f"{mins / 60:g}小时" if isinstance(mins, (int, float)) and mins % 60 == 0 else "额度")
        reset = window.get("resetsAt")
        value = window.get("remainingPercent")
        if isinstance(reset, (int, float)) and reset <= now:
            parts.append(label + "待更新")
        elif isinstance(value, (int, float)):
            parts.append(f"{label}剩余 {value:g}%")
        else:
            parts.append(label + "未提供")
    text = "SSH · " + (" · ".join(parts) if parts else "额度未提供")
    if data.get("warning"):
        text += " · 缓存"
    return text


def main():
    if os.name != "nt":
        print("The main-window HUD requires Windows.", file=sys.stderr)
        return 1
    import ctypes
    from ctypes import wintypes
    import tkinter as tk

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    existing = user.FindWindowW(None, TITLE)
    if "--stop" in sys.argv:
        if existing:
            user.PostMessageW(existing, 0x0010, 0, 0)
            deadline = time.monotonic() + 3
            while user.FindWindowW(None, TITLE) and time.monotonic() < deadline:
                time.sleep(0.05)
        return 0
    if existing:
        return 0
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    if hasattr(user, "GetDpiForWindow"):
        user.GetDpiForWindow.argtypes = [wintypes.HWND]
        user.GetDpiForWindow.restype = wintypes.UINT

    def process_id(hwnd):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def is_client(hwnd):
        handle = kernel.OpenProcess(0x1000, False, process_id(hwnd))
        if not handle:
            return False
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            return bool(kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))) and (
                Path(path.value).name.lower() in ("chatgpt.exe", "codex.exe"))
        finally:
            kernel.CloseHandle(handle)

    root = tk.Tk()
    root.title(TITLE)
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#39433f")
    label = tk.Label(root, text="SSH · 读取额度…", bg="#242826", fg="#9ee0c0",
                     padx=12, pady=3, cursor="hand2")
    label.pack(fill="both", expand=True, padx=1, pady=1)
    root.update_idletasks()
    hud_hwnd = user.GetAncestor(root.winfo_id(), 2)
    getter = user.GetWindowLongPtrW if hasattr(user, "GetWindowLongPtrW") else user.GetWindowLongW
    setter = user.SetWindowLongPtrW if hasattr(user, "SetWindowLongPtrW") else user.SetWindowLongW
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    setter.restype = ctypes.c_ssize_t
    setter(hud_hwnd, -20, getter(hud_hwnd, -20) | 0x08000000 | 0x00000080)

    messages = queue.Queue(maxsize=1)
    state = {"fetching": False, "nextRead": 0, "target": None, "data": None}

    def refresh(force=False):
        if state["fetching"]:
            return
        state["fetching"] = True
        def work():
            try:
                messages.put({"data": server.quota(force=force)})
            except Exception:
                messages.put({"error": "SSH · 查询失败，右键重试"})
        threading.Thread(target=work, daemon=True).start()

    def details(_event=None):
        os.startfile(DETAIL_URL)

    menu = tk.Menu(root, tearoff=False)
    menu.add_command(label="刷新额度快照", command=lambda: refresh(True))
    menu.add_command(label="打开详细面板", command=details)
    menu.add_separator()
    menu.add_command(label="关闭额度条", command=root.destroy)
    label.bind("<Button-1>", details)
    def popup(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    label.bind("<Button-3>", popup)

    def tick():
        try:
            message = messages.get_nowait()
        except queue.Empty:
            message = None
        if message:
            state["fetching"] = False
            state["nextRead"] = time.monotonic() + server.CACHE_SECONDS
            if "data" in message:
                state["data"] = message["data"]
                label.configure(text=summary(message["data"]), fg="#9ee0c0")
            else:
                label.configure(text=message["error"], fg="#e8bf86")
        if time.monotonic() >= state["nextRead"]:
            refresh()
        fg = user.GetForegroundWindow()
        target = user.GetAncestor(fg, 3) if fg else None
        if target and is_client(target):
            state["target"] = target
        elif fg and process_id(fg) == os.getpid():
            target = state["target"]
        else:
            target = None
        if not target or not user.IsWindowVisible(target) or user.IsIconic(target):
            root.withdraw()
        else:
            rect = wintypes.RECT()
            user.GetWindowRect(target, ctypes.byref(rect))
            try:
                ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(
                    ctypes.c_void_p(target), 9, ctypes.byref(rect), ctypes.sizeof(rect))
            except OSError:
                pass
            dpi = user.GetDpiForWindow(target) if hasattr(user, "GetDpiForWindow") else 96
            scale = max(1, dpi / 96)
            label.configure(font=("Microsoft YaHei UI", -round(13 * scale)))
            root.update_idletasks()
            width = max(round(210 * scale), label.winfo_reqwidth() + 2)
            height = round(29 * scale)
            x = max(rect.left + round(260 * scale), rect.right - round(155 * scale) - width)
            y = rect.top + round(6 * scale)
            root.geometry(f"{width}x{height}+{x}+{y}")
            if not root.winfo_viewable():
                root.deiconify()
            user.SetWindowPos(hud_hwnd, ctypes.c_void_p(-1), x, y, width, height, 0x0010 | 0x0040)
        root.after(250, tick)

    root.after(0, tick)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
