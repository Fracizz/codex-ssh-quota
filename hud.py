"""Optional Windows title-area companion. Uses Tk/Win32; does not inject into Codex."""
import os
from pathlib import Path
import queue
import sys
import threading
import time

import server
import source_catalog

TITLE = "Codex SSH Quota HUD"
DETAIL_URL = "codex://plugins/codex-ssh-quota@codex-ssh-quota/app/codex_quota"


def is_main_window(class_name, style, owner, executable):
    """Exclude native dialogs and Electron utility windows, even in the same process."""
    return (class_name == "Chrome_WidgetWin_1" and not owner
            and executable.lower() in ("chatgpt.exe", "codex.exe")
            and bool(style & 0x00040000)  # WS_THICKFRAME: resizable application window
            and not style & (0x40000000 | 0x08000000))  # WS_CHILD / WS_DISABLED


def hud_geometry(rect, width, height, scale):
    left, top, right, bottom = rect
    x = right - round(155 * scale) - width
    y = top + round(7 * scale)
    if x < left + round(260 * scale) or y + height > bottom:
        return None
    return width, height, x, y


def button_png(width, height, radius, fill, background, border):
    """Antialiased RGB button background; native Tk renders the text above it."""
    import math
    import struct
    import zlib
    def rgb(value):
        return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))
    inner, outer, edge = rgb(fill), rgb(background), rgb(border)
    def coverage(x, y, inset):
        r = max(0, radius-inset)
        qx = abs(x-width/2) - (width/2-inset-r)
        qy = abs(y-height/2) - (height/2-inset-r)
        distance = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
        return max(0, min(1, 0.5-distance))
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            outside, inside = coverage(x+0.5, y+0.5, 0), coverage(x+0.5, y+0.5, 1)
            pixels.extend(round(outer[c]*(1-outside) + edge[c]*(outside-inside) + inner[c]*inside) for c in range(3))
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind+payload) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b"")


def window_items(data, now=None):
    now = time.time() if now is None else now
    buckets = data.get("buckets") or []
    bucket = next((b for b in buckets if b.get("id") == "codex"), buckets[0] if buckets else {})
    items = []
    for window in bucket.get("windows", [])[:2]:
        mins = window.get("durationMins")
        label = "本周" if mins == 10080 else "5小时" if mins == 300 else (
            f"{mins / 60:g}小时" if isinstance(mins, (int, float)) and mins % 60 == 0 else "额度")
        reset = window.get("resetsAt")
        value = window.get("remainingPercent")
        if isinstance(reset, (int, float)) and reset <= now:
            items.append({"label": label, "value": "待更新", "remaining": None, "durationMins": mins, "resetsAt": reset})
        elif isinstance(value, (int, float)):
            items.append({"label": label + "剩余", "value": f"{value:g}%", "remaining": value, "durationMins": mins, "resetsAt": reset})
        else:
            items.append({"label": label, "value": "暂无记录", "remaining": None, "durationMins": mins, "resetsAt": reset})
    return items or [{"label": "额度", "value": "暂无记录", "remaining": None}]


def weekly_item(data, now=None):
    return next((item for item in window_items(data, now) if item.get("durationMins") == 10080),
                {"label": "周额度", "value": "暂无记录", "remaining": None, "resetsAt": None})


def source_label(data):
    value = data.get("sourceName") or data.get("sshHost") or data.get("host") or "SSH"
    if str(value).startswith("local:"):
        value = data.get("host") or "SSH"
    text = " ".join(str(value).split())
    return text if len(text) <= 20 else text[:19] + "…"


def reset_label(data, now=None):
    from datetime import datetime
    now = time.time() if now is None else now
    stamp = weekly_item(data, now).get("resetsAt")
    if not isinstance(stamp, (int, float)):
        return "时间未提供", ""
    try:
        text = datetime.fromtimestamp(stamp).strftime("%m/%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return "时间未提供", ""
    return text, "重置" if stamp > now else "已到期"


def summary(data, now=None):
    item = weekly_item(data, now)
    reset, suffix = reset_label(data, now)
    text = f"{source_label(data)} · {item['label']} {item['value']} · {reset} {suffix}".rstrip()
    return text + (" · 缓存" if data.get("warning") else "")


def accent(data, now=None):
    value = weekly_item(data, now)["remaining"]
    if data.get("warning"):
        return "#d6ae71"
    if value is None:
        return "#9299a3"
    return "#e59393" if value <= 10 else "#d6ae71" if value <= 25 else "#89cbb1"


def tooltip_text(data, error=None):
    from datetime import datetime
    if not data:
        return (error or "正在读取远端额度记录") + "\n右键可刷新额度"
    source = data.get("sshHost") or data.get("host") or "SSH 环境"
    if data.get("sourceMode") == "local":
        source = data.get("host") or "当前 SSH 主机"
    stamp = data.get("snapshotRecordedAt")
    try:
        captured = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%m月%d日 %H:%M")
    except (AttributeError, ValueError):
        captured = "未知时间"
    status = "当前显示上次保存的记录" if data.get("warning") or error else "约每 10 分钟更新一次"
    reset, suffix = reset_label(data)
    return f"{source_label(data)} · 额度快照\n{str(source)[:120]}\n周额度 {reset} {suffix}\n记录于 {captured}\n{status}\n\n点击名称切换主机 · 点击额度查看详情"



def accept_source_result(state, message):
    source_id = message["sourceId"]
    state["pending"].discard(source_id)
    if message.get("data") and message["data"].get("sourceId") != source_id:
        message = {"sourceId": source_id, "error": "来源校验未通过，请重新查询。"}
    previous = state["results"].get(source_id, {})
    if "error" in message and previous.get("data"):
        message = {**message, "data": {**previous["data"], "warning": message["error"]}}
    state["results"][source_id] = message
    state["nextReads"][source_id] = time.monotonic() + server.CACHE_SECONDS
    if len(state["results"]) > source_catalog.MAX_SOURCES:
        old = next(key for key in state["results"] if key != state["source"]["id"])
        del state["results"][old]
        state["nextReads"].pop(old, None)
    if source_id != state["source"]["id"]:
        return False
    state["data"] = message.get("data")
    state["error"] = message.get("error")
    return True

def main():
    if os.name != "nt":
        print("The main-window HUD requires Windows.", file=sys.stderr)
        return 1
    import ctypes
    from ctypes import wintypes
    import tkinter as tk
    import tkinter.font as tkfont

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
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
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
    getter = user.GetWindowLongPtrW if hasattr(user, "GetWindowLongPtrW") else user.GetWindowLongW
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t

    def process_id(hwnd):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def is_client_main(hwnd):
        if not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
            return False
        class_name = ctypes.create_unicode_buffer(128)
        user.GetClassNameW(hwnd, class_name, len(class_name))
        style, owner = getter(hwnd, -16), user.GetWindow(hwnd, 4)  # GW_OWNER
        handle = kernel.OpenProcess(0x1000, False, process_id(hwnd))
        if not handle:
            return False
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            return bool(kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))) and is_main_window(
                class_name.value, style, owner, Path(path.value).name)
        finally:
            kernel.CloseHandle(handle)

    root = tk.Tk()
    root.title(TITLE)
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#353439")
    strip = tk.Frame(root, bg="#242326", borderwidth=0, highlightthickness=0, cursor="hand2")
    strip.pack(fill="both", expand=True, padx=1, pady=1)
    name_label = tk.Label(strip, bg="#242326", fg="#b5b8c0", borderwidth=0, padx=0, pady=0, cursor="hand2")
    quota_label = tk.Label(strip, bg="#242326", fg="#edf0f4", borderwidth=0, padx=0, pady=0, cursor="hand2")
    reset_time_label = tk.Label(strip, bg="#242326", fg="#a6abb5", borderwidth=0, padx=0, pady=0, cursor="hand2")
    name_label.pack(side="left")
    quota_label.pack(side="left")
    reset_time_label.pack(side="left")
    query_button = tk.Button(strip, text="查询", relief="flat", borderwidth=0,
                             bg="#36353a", fg="#dce0e7", activebackground="#45444b",
                             activeforeground="#ffffff", disabledforeground="#8b8d96",
                             highlightthickness=0, cursor="hand2", padx=8, pady=1)
    query_button.pack(side="left")
    root.update_idletasks()
    hud_hwnd = user.GetAncestor(root.winfo_id(), 2)
    setter = user.SetWindowLongPtrW if hasattr(user, "SetWindowLongPtrW") else user.SetWindowLongW
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    setter.restype = ctypes.c_ssize_t
    setter(hud_hwnd, -20, getter(hud_hwnd, -20) | 0x08000000 | 0x00000080)
    try:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        corner = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hud_hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    except OSError:
        pass

    messages = queue.Queue(maxsize=source_catalog.MAX_SOURCES * 2)
    state = {"fetching": False, "target": None, "data": None,
             "source": server.resolve_source(), "pending": set(), "results": {}, "nextReads": {},
             "error": None, "hover": False, "paintKey": None, "geometry": None,
             "tooltip": None, "hoverTimer": None, "queryHover": False, "buttonImages": {}, "buttonImageKey": None,
             "scale": max(1, user.GetDpiForWindow(hud_hwnd) / 96) if hasattr(user, "GetDpiForWindow") else 1}
    label_font = tkfont.Font(family="Microsoft YaHei UI", size=-12)
    source_font = tkfont.Font(family="Segoe UI", size=-10, weight="bold")

    def dismiss_tooltip():
        if state["hoverTimer"]:
            root.after_cancel(state["hoverTimer"])
            state["hoverTimer"] = None
        if state["tooltip"]:
            state["tooltip"].destroy()
            state["tooltip"] = None

    def show_tooltip():
        state["hoverTimer"] = None
        if not state["hover"] or not root.winfo_viewable():
            return
        tip = tk.Toplevel(root)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.configure(bg="#41464e")
        content = tk.Frame(tip, bg="#272a30")
        content.pack(padx=1, pady=1)
        heading, _, body = tooltip_text(state["data"], state["error"]).partition("\n")
        tk.Label(content, text=heading, bg="#272a30", fg="#edf1f6", anchor="w",
                 font=("Microsoft YaHei UI", -round(12 * state["scale"]), "bold")).pack(anchor="w", padx=14, pady=(12, 5))
        tk.Label(content, text=body, bg="#272a30", fg="#adb5c3", justify="left",
                 wraplength=round(300 * state["scale"]),
                 font=("Microsoft YaHei UI", -round(11 * state["scale"]))).pack(anchor="w", padx=14, pady=(0, 12))
        tip.update_idletasks()
        tip_hwnd = user.GetAncestor(tip.winfo_id(), 2)
        setter(tip_hwnd, -20, getter(tip_hwnd, -20) | 0x08000000 | 0x00000080)
        tip.geometry(f"+{root.winfo_x() + root.winfo_width() - tip.winfo_reqwidth()}+{root.winfo_y() + root.winfo_height() + 7}")
        state["tooltip"] = tip
        tip.deiconify()

    def hover(on):
        state["hover"] = on
        dismiss_tooltip()
        if on:
            state["hoverTimer"] = root.after(500, show_tooltip)

    def paint(scale):
        data = {**(state["data"] or {}), "sourceName": state["source"]["sourceName"]}
        if state["error"]:
            data = {**data, "warning": state["error"]}
        item = weekly_item(data)
        if state["data"] is None:
            item = {"label": "周额度", "value": "暂不可用" if state["error"] else "读取中", "remaining": None}
        name = source_label(data)
        reset, suffix = reset_label(data)
        if state["fetching"] and state["data"] is None:
            reset, suffix = "正在读取", ""
        key = (str(item), name, reset, suffix, bool(data.get("warning")), state["fetching"], state["hover"], state["queryHover"], scale)
        if state["paintKey"] == key:
            return state["size"]
        state["paintKey"] = key
        label_font.configure(size=-round(12 * scale), weight="normal")
        bg = "#2d2c30" if state["hover"] else "#242326"
        strip.configure(bg=bg)
        name_label.configure(text=name, font=label_font, bg=bg)
        remaining = item["remaining"]
        color = accent(data) if remaining is not None and remaining <= 25 or data.get("warning") else "#edf0f4"
        if remaining is None:
            color = "#a6abb5"
        quota_label.configure(text=item["label"].replace("本周剩余", "周剩余") + " " + item["value"],
                              font=label_font, bg=bg, fg=color)
        reset_time_label.configure(text=(reset + " " + suffix).strip(), font=label_font, bg=bg)
        name_label.pack_configure(padx=(round(10*scale), 0))
        quota_label.pack_configure(padx=(round(16*scale), 0))
        reset_time_label.pack_configure(padx=(round(16*scale), 0))
        image_key = scale, bg
        if state["buttonImageKey"] != image_key:
            import base64
            width, height, radius = round(55*scale), round(21*scale), round(6*scale)
            state["buttonImages"] = {
                "normal": tk.PhotoImage(data=base64.b64encode(button_png(width, height, radius, "#39383e", bg, "#4b4952"))),
                "hover": tk.PhotoImage(data=base64.b64encode(button_png(width, height, radius, "#45434b", bg, "#62606c"))),
                "disabled": tk.PhotoImage(data=base64.b64encode(button_png(width, height, radius, "#302f34", bg, "#3d3b43")))
            }
            state["buttonImageKey"] = image_key
        image_state = "disabled" if state["fetching"] else "hover" if state["queryHover"] else "normal"
        query_button.configure(text="查询中" if state["fetching"] else "查询",
                               state="disabled" if state["fetching"] else "normal",
                               font=("Microsoft YaHei UI", -round(11*scale)),
                               image=state["buttonImages"][image_state], compound="center",
                               bg=bg, activebackground=bg, padx=0, pady=0, width=0, height=0)
        query_button.pack_configure(padx=(round(12*scale), round(6*scale)), pady=round(3*scale))
        root.update_idletasks()
        width, height = strip.winfo_reqwidth() + 2, round(28 * scale)
        state["size"] = width, height
        return width, height

    query_slots = threading.Semaphore(4)
    def refresh(force=False):
        source_id = state["source"]["id"]
        if source_id in state["pending"]:
            return
        if len(state["pending"]) >= source_catalog.MAX_SOURCES:
            state["error"] = "查询较多，请稍后重试。"
            state["nextReads"][source_id] = time.monotonic()+1
            return
        state["pending"].add(source_id)
        state["fetching"] = True
        dismiss_tooltip()
        def work():
            with query_slots:
                try:
                    data = server.quota(force=force, source=source_id)
                    messages.put({"sourceId": source_id, "data": data})
                except Exception:
                    messages.put({"sourceId": source_id, "error": "暂时无法读取此主机额度，请点击查询重试"})
        threading.Thread(target=work, daemon=True).start()

    selected = tk.StringVar(value=state["source"]["id"])
    source_menu = tk.Menu(root, tearoff=False, bg="#282c32", fg="#dce1e9", activebackground="#3c434d",
                         activeforeground="#ffffff", selectcolor="#bdcbe0", relief="flat", borderwidth=1)

    def choose_source(source_id):
        try:
            entry = server.select_source(source_id)
        except server.QuotaError as exc:
            selected.set(state["source"]["id"])
            state["error"] = str(exc)
            return
        state["source"] = entry
        selected.set(entry["id"])
        previous = state["results"].get(entry["id"], {})
        state["data"] = previous.get("data")
        state["error"] = previous.get("error")
        state["fetching"] = entry["id"] in state["pending"]
        state["nextReads"][entry["id"]] = 0
        state["paintKey"] = None
        dismiss_tooltip()

    def switch_menu(event):
        dismiss_tooltip()
        entries, omitted = server.available_sources()
        source_menu.delete(0, "end")
        source_menu.configure(font=("Microsoft YaHei UI", -round(12 * state["scale"])))
        names = [entry["sourceName"] for entry in entries]
        for entry in entries:
            label = entry["sourceName"]
            if names.count(label) > 1:
                label += " (" + entry["sshHost"] + ")"
            label = label if len(label) <= 48 else label[:47] + "…"
            source_menu.add_radiobutton(label=label, variable=selected, value=entry["id"],
                                        command=lambda key=entry["id"]: choose_source(key))
        if omitted:
            source_menu.add_separator()
            source_menu.add_command(label=f"另有 {omitted} 项，请在配置中精简来源", state="disabled")
        try:
            source_menu.tk_popup(name_label.winfo_rootx(), root.winfo_y()+root.winfo_height()+4)
        finally:
            source_menu.grab_release()

    def details(_event=None):
        dismiss_tooltip()
        from urllib.parse import quote
        path = quote("/source/"+state["source"]["id"], safe="")
        os.startfile(DETAIL_URL+"?path="+path)

    query_button.configure(command=lambda: refresh(True))

    menu = tk.Menu(root, tearoff=False, bg="#282c32", fg="#dce1e9", activebackground="#3c434d",
                   activeforeground="#ffffff", relief="flat", borderwidth=1)
    menu.add_command(label="刷新额度", command=lambda: refresh(True))
    menu.add_command(label="查看详情", command=details)
    menu.add_separator()
    menu.add_command(label="关闭额度条", command=root.destroy)
    def popup(event):
        menu.configure(font=("Microsoft YaHei UI", -round(12 * state["scale"])))
        dismiss_tooltip()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    def check_leave():
        x, y = root.winfo_pointerxy()
        if not (root.winfo_x() <= x < root.winfo_x()+root.winfo_width() and
                root.winfo_y() <= y < root.winfo_y()+root.winfo_height()):
            hover(False)
    name_label.bind("<Button-1>", switch_menu)
    name_label.bind("<Button-3>", popup)
    name_label.bind("<Enter>", lambda _event: hover(True))
    name_label.bind("<Leave>", lambda _event: root.after(30, check_leave))
    for widget in (strip, quota_label, reset_time_label):
        widget.bind("<Button-1>", details)
        widget.bind("<Button-3>", popup)
        widget.bind("<Enter>", lambda _event: hover(True))
        widget.bind("<Leave>", lambda _event: root.after(30, check_leave))
    def query_enter(_event):
        state["hover"] = True
        state["queryHover"] = True
        dismiss_tooltip()
    def query_leave(_event):
        state["queryHover"] = False
        root.after(30, check_leave)
    query_button.bind("<Enter>", query_enter)
    query_button.bind("<Leave>", query_leave)

    def hide():
        dismiss_tooltip()
        source_menu.unpost()
        menu.unpost()
        root.withdraw()

    def tick():
        for _ in range(source_catalog.MAX_SOURCES * 2):
            try:
                message = messages.get_nowait()
            except queue.Empty:
                break
            accept_source_result(state, message)
        current = state["source"]["id"]
        state["fetching"] = current in state["pending"]
        if time.monotonic() >= state["nextReads"].get(current, 0):
            refresh()
        width, height = paint(state["scale"])
        if state["geometry"] is None:
            root.geometry(f"{width}x{height}")
            root.update_idletasks()
        fg = user.GetForegroundWindow()
        target = user.GetAncestor(fg, 2) if fg else None  # GA_ROOT, never a dialog's root owner
        if target and is_client_main(target):
            state["target"] = target
        elif fg and process_id(fg) == os.getpid():
            target = state["target"]
        else:
            target = None
        if not target or not is_client_main(target):
            hide()
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
            state["scale"] = scale
            width, height = paint(scale)
            geometry = hud_geometry((rect.left, rect.top, rect.right, rect.bottom), width, height, scale)
            if geometry is None:
                hide()
                root.after(250, tick)
                return
            _, _, x, y = geometry
            if state["geometry"] != geometry:
                state["geometry"] = geometry
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
