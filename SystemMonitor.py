import ctypes
import gc
import json
import math
import os
import threading
import sys
import time
import shutil
import subprocess
import struct
from dataclasses import dataclass
from typing import Optional, Tuple

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pystray
except ImportError:
    pystray = None

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

try:
    import tkinter as tk
except ImportError:
    tk = None

try:
    import winreg
except ImportError:
    winreg = None

try:
    import wmi
except ImportError:
    wmi = None

try:
    import pythoncom
except ImportError:
    pythoncom = None


APP_NAME = "Laptop Monitor"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
CONFIG_FILE_NAME = "tray_monitor_settings.json"
INSTALL_SCRIPT_NAME = "SystemMonitor.py"
SCHEDULED_TASK_NAME = "SystemMonitor"

DEBUG_MODE = False


def debug_log(message: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")

def _build_launch_command(script_path: str, pythonw_path: Optional[str] = None) -> str:
    resolved_script = os.path.abspath(script_path)
    resolved_pythonw = pythonw_path
    if not resolved_pythonw:
        executable = os.path.abspath(sys.executable)
        directory, name = os.path.split(executable)
        if name.lower() == "python.exe":
            candidate = os.path.join(directory, "pythonw.exe")
            resolved_pythonw = candidate if os.path.exists(candidate) else executable
        else:
            resolved_pythonw = executable
    return f'"{resolved_pythonw}" "{resolved_script}"'


def _set_startup_run_key(command: str):
    if winreg is None:
        return
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH)
    try:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(key)
FONT_COLORS = {
    "White": "#f3f3f3",
    "Black": "#000000",
    "Green": "#22c55e",
    "Cyan": "#22d3ee",
    "Yellow": "#facc15",
    "Red": "#ef4444",
}

BG_COLORS = {
    "Dark": "#111111",
    "Slate": "#1e293b",
    "Navy": "#0f172a",
    "Maroon": "#3f1d1d",
    "Forest": "#1b4332",
    "Light": "#f5f5f5",
}

FONT_FAMILIES = [
    "Segoe UI",
    "Consolas",
    "Arial",
]


@dataclass
class AppState:
    temp_unit: str = "C"
    refresh_seconds: int = 2
    running: bool = True
    show_panel: bool = True
    font_color: str = "#f3f3f3"
    bg_color: str = "#111111"
    transparent_bg: bool = True
    font_family: str = "Segoe UI"
    show_cpu: bool = True
    show_ram: bool = True
    show_temp: bool = True
    show_fps: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "AppState":
        state = cls()
        if not isinstance(data, dict):
            return state

        unit = data.get("temp_unit")
        if unit in ["C", "F"]:
            state.temp_unit = unit

        refresh = data.get("refresh_seconds")
        if isinstance(refresh, int) and refresh in [1, 2, 5]:
            state.refresh_seconds = refresh

        if isinstance(data.get("show_panel"), bool):
            state.show_panel = data["show_panel"]
        if isinstance(data.get("transparent_bg"), bool):
            state.transparent_bg = data["transparent_bg"]
        if isinstance(data.get("show_cpu"), bool):
            state.show_cpu = data["show_cpu"]
        if isinstance(data.get("show_ram"), bool):
            state.show_ram = data["show_ram"]
        if isinstance(data.get("show_temp"), bool):
            state.show_temp = data["show_temp"]
        if isinstance(data.get("show_fps"), bool):
            state.show_fps = data["show_fps"]

        font_color = data.get("font_color")
        if isinstance(font_color, str) and font_color:
            state.font_color = font_color

        bg_color = data.get("bg_color")
        if isinstance(bg_color, str) and bg_color:
            state.bg_color = bg_color

        font_family = data.get("font_family")
        if isinstance(font_family, str) and font_family in FONT_FAMILIES:
            state.font_family = font_family

        return state

    def to_dict(self) -> dict:
        return {
            "temp_unit": self.temp_unit,
            "refresh_seconds": self.refresh_seconds,
            "show_panel": self.show_panel,
            "font_color": self.font_color,
            "bg_color": self.bg_color,
            "transparent_bg": self.transparent_bg,
            "font_family": self.font_family,
            "show_cpu": self.show_cpu,
            "show_ram": self.show_ram,
            "show_temp": self.show_temp,
            "show_fps": self.show_fps,
        }


class TemperatureReader:
    def __init__(self):
        self._standard_wmi = self._connect_wmi("root\\wmi")
        self._lhm_wmi = self._connect_wmi("root\\LibreHardwareMonitor")
        self._ohm_wmi = self._connect_wmi("root\\OpenHardwareMonitor")

    def _connect_wmi(self, namespace: str):
        if wmi is None:
            return None
        try:
            return wmi.WMI(namespace=namespace)
        except Exception:
            return None

    def get_temperature_reading(self) -> Tuple[Optional[float], str]:
        sources = [
            ("Libre/OpenHardwareMonitor", self._read_libre_or_open_hw_temp),
            ("psutil", self._read_psutil_temp),
            ("ACPI", self._read_acpi_temp),
        ]
        for source_name, source in sources:
            try:
                value = source()
                if value is not None:
                    debug_log(f"Temperature source '{source_name}' returned {value:.2f}C")
                    return value, source_name
                debug_log(f"Temperature source '{source_name}' returned no value")
            except Exception as exc:
                debug_log(f"Temperature source '{source_name}' failed: {exc}")
                continue
        return None, "Unavailable"

    def _valid_temp(self, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if math.isnan(value):
            return None
        if -20 <= value <= 130:
            return value
        return None

    def _read_libre_or_open_hw_temp(self) -> Optional[float]:
        for client in [self._lhm_wmi, self._ohm_wmi]:
            if client is None:
                continue

            sensors = client.Sensor()

            cpu_temps = []
            any_temps = []
            for sensor in sensors:
                sensor_type = str(getattr(sensor, "SensorType", "")).lower()
                if "temp" not in sensor_type:
                    continue

                raw_value = getattr(sensor, "Value", None)
                try:
                    temp = float(raw_value)
                except (TypeError, ValueError):
                    continue

                temp = self._valid_temp(temp)
                if temp is None:
                    continue

                any_temps.append(temp)
                name = str(getattr(sensor, "Name", "")).lower()
                identifier = str(getattr(sensor, "Identifier", "")).lower()
                if "cpu" in name or "cpu" in identifier or "package" in name:
                    cpu_temps.append(temp)

            if cpu_temps:
                return max(cpu_temps)
            if any_temps:
                return max(any_temps)

        return None

    def _read_psutil_temp(self) -> Optional[float]:
        if not hasattr(psutil, "sensors_temperatures"):
            return None

        try:
            all_temps = psutil.sensors_temperatures(fahrenheit=False)
        except Exception:
            return None

        if not all_temps:
            return None

        cpu_like = []
        any_temps = []

        for group_name, entries in all_temps.items():
            group_lower = str(group_name).lower()
            for entry in entries:
                current = self._valid_temp(getattr(entry, "current", None))
                if current is None:
                    continue

                any_temps.append(current)
                label = str(getattr(entry, "label", "")).lower()
                if "cpu" in label or "package" in label or "core" in label or "cpu" in group_lower:
                    cpu_like.append(current)

        if cpu_like:
            return max(cpu_like)
        if any_temps:
            return max(any_temps)
        return None

    def _read_acpi_temp(self) -> Optional[float]:
        if self._standard_wmi is None:
            return None

        zones = self._standard_wmi.MSAcpi_ThermalZoneTemperature()
        if not zones:
            return None

        values = []
        for zone in zones:
            kelvin_tenths = getattr(zone, "CurrentTemperature", None)
            if kelvin_tenths is None:
                continue
            celsius = (kelvin_tenths / 10.0) - 273.15
            celsius = self._valid_temp(celsius)
            if celsius is not None:
                values.append(celsius)

        return max(values) if values else None


class MiniPanel:
    def __init__(self, get_text_callback, is_visible_callback, get_style_callback):
        self._get_text = get_text_callback
        self._is_visible = is_visible_callback
        self._get_style = get_style_callback
        self._root = None
        self._label = None
        self._shadow_label = None
        self._text_frame = None
        self._started = False
        self._last_style = None

    def _is_light_color(self, color: str) -> bool:
        if not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
            return True
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
        except ValueError:
            return True
        luminance = (0.299 * r) + (0.587 * g) + (0.114 * b)
        return luminance >= 160

    def _shadow_color_for(self, font_color: str) -> str:
        return "#000000" if self._is_light_color(font_color) else "#f3f3f3"

    def start(self):
        if tk is None or self._started:
            return
        self._started = True
        threading.Thread(target=self._run_ui, daemon=True).start()

    def _run_ui(self):
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)

        self._text_frame = tk.Frame(self._root, padx=10, pady=6)
        self._text_frame.pack()

        self._shadow_label = tk.Label(
            self._text_frame,
            text="Starting...",
            borderwidth=0,
        )
        self._label = tk.Label(
            self._text_frame,
            text="Starting...",
            padx=10,
            pady=6,
            borderwidth=0,
        )
        self._label.pack()
        self._shadow_label.place(x=1, y=1)

        self._apply_style(force=True)
        self._place_bottom_right()
        self._refresh()
        self._root.mainloop()

    def _apply_style(self, force: bool = False):
        if self._root is None or self._label is None or self._shadow_label is None or self._text_frame is None:
            return

        style = self._get_style()
        key = (
            style["font_color"],
            style["bg_color"],
            style["transparent_bg"],
            style["font_family"],
        )

        if not force and key == self._last_style:
            return

        self._last_style = key
        self._root.configure(bg=style["bg_color"])
        self._text_frame.configure(bg=style["bg_color"])

        font_spec = (style["font_family"], 9, "bold")
        self._label.configure(
            fg=style["font_color"],
            bg=style["bg_color"],
            font=font_spec,
        )

        shadow_color = self._shadow_color_for(style["font_color"])
        self._shadow_label.configure(
            fg=shadow_color,
            bg=style["bg_color"],
            font=font_spec,
        )

        if style["transparent_bg"]:
            self._shadow_label.place(x=1, y=1)
            self._shadow_label.lift()
            self._label.lift()
        else:
            self._shadow_label.place_forget()

        try:
            if style["transparent_bg"]:
                self._root.wm_attributes("-transparentcolor", style["bg_color"])
            else:
                self._root.wm_attributes("-transparentcolor", "")
        except Exception:
            pass

    def _place_bottom_right(self):
        if self._root is None:
            return
        self._root.update_idletasks()
        width = self._root.winfo_reqwidth()
        height = self._root.winfo_reqheight()
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = screen_w - width - 18
        y = screen_h - height - 60
        self._root.geometry(f"+{x}+{y}")

    def _refresh(self):
        if self._root is None or self._label is None or self._shadow_label is None or self._text_frame is None:
            return

        self._apply_style()

        if self._is_visible():
            self._root.deiconify()
            latest_text = self._get_text()
            self._label.config(text=latest_text)
            self._shadow_label.config(text=latest_text)
            self._place_bottom_right()
        else:
            self._root.withdraw()

        self._root.after(800, self._refresh)

    def stop(self):
        if self._root is not None:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

class TrayMonitor:
    def __init__(self):
        if pystray is None or Image is None or ImageDraw is None or psutil is None:
            raise RuntimeError("Missing required runtime packages. Run startup checks output for details.")

        self.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE_NAME)
        self.state = self._load_settings()
        self.temperature_reader = None
        self.temp_source = "Initializing"
        self.latest_text = "CPU --%   RAM --%   TEMP --"
        self.latest_fps = 0.0
        self.icon = pystray.Icon(APP_NAME, self._create_icon(), APP_NAME, menu=self._build_menu())
        self._worker_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.panel = MiniPanel(self._get_latest_text, self._panel_is_visible, self._get_panel_style)

    def run(self):
        self.panel.start()
        self._worker_thread.start()
        self.icon.run()

    def _load_settings(self) -> AppState:
        if not os.path.exists(self.config_path):
            return AppState()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppState.from_dict(data)
        except Exception:
            return AppState()

    def _save_settings(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.state.to_dict(), f, indent=2)
        except Exception:
            pass

    def _persist_and_refresh_menu(self):
        self._save_settings()
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda item: f"Temp Source: {self.temp_source}", self._noop, enabled=False),
            pystray.MenuItem("Display Metrics", pystray.Menu(
                pystray.MenuItem("Show CPU", self._toggle_show_cpu, checked=lambda item: self.state.show_cpu),
                pystray.MenuItem("Show RAM", self._toggle_show_ram, checked=lambda item: self.state.show_ram),
                pystray.MenuItem("Show Temp", self._toggle_show_temp, checked=lambda item: self.state.show_temp),
                pystray.MenuItem("Show FPS", self._toggle_show_fps, checked=lambda item: self.state.show_fps),
            )),
            pystray.MenuItem("Temperature Unit", pystray.Menu(
                pystray.MenuItem("Celsius", self._set_celsius, checked=lambda item: self.state.temp_unit == "C"),
                pystray.MenuItem("Fahrenheit", self._set_fahrenheit, checked=lambda item: self.state.temp_unit == "F"),
            )),
            pystray.MenuItem("Refresh", pystray.Menu(
                pystray.MenuItem("1 sec", lambda icon, item: self._set_refresh(1), checked=lambda item: self.state.refresh_seconds == 1),
                pystray.MenuItem("2 sec", lambda icon, item: self._set_refresh(2), checked=lambda item: self.state.refresh_seconds == 2),
                pystray.MenuItem("5 sec", lambda icon, item: self._set_refresh(5), checked=lambda item: self.state.refresh_seconds == 5),
            )),
            pystray.MenuItem("Font Family", pystray.Menu(*[
                pystray.MenuItem(name, self._make_set_font_family_action(name), checked=self._make_font_family_checked(name))
                for name in FONT_FAMILIES
            ])),
            pystray.MenuItem("Font Color", pystray.Menu(*[
                pystray.MenuItem(name, self._make_set_font_color_action(value), checked=self._make_font_color_checked(value))
                for name, value in FONT_COLORS.items()
            ])),
            pystray.MenuItem("Background Color", pystray.Menu(*[
                pystray.MenuItem(name, self._make_set_bg_color_action(value), checked=self._make_bg_color_checked(value))
                for name, value in BG_COLORS.items()
            ])),
            pystray.MenuItem("Transparent Background", self._toggle_transparent_bg, checked=lambda item: self.state.transparent_bg),
            pystray.MenuItem("Show Mini Panel", self._toggle_panel, checked=lambda item: self.state.show_panel),
            pystray.MenuItem("Start With Windows", self._toggle_startup, checked=lambda item: self._is_startup_enabled()),
            pystray.MenuItem("Quit", self._quit),
        )

    def _noop(self, icon=None, item=None):
        return None

    def _make_set_font_family_action(self, family: str):
        def _action(icon, item):
            self._set_font_family(family)
        return _action

    def _make_font_family_checked(self, family: str):
        def _checked(item):
            return self.state.font_family == family
        return _checked

    def _make_set_font_color_action(self, color: str):
        def _action(icon, item):
            self._set_font_color(color)
        return _action

    def _make_font_color_checked(self, color: str):
        def _checked(item):
            return self.state.font_color.lower() == color.lower()
        return _checked

    def _make_set_bg_color_action(self, color: str):
        def _action(icon, item):
            self._set_bg_color(color)
        return _action

    def _make_bg_color_checked(self, color: str):
        def _checked(item):
            return self.state.bg_color.lower() == color.lower()
        return _checked

    def _create_icon(self):
        image = Image.new("RGB", (64, 64), "#20252b")
        draw = ImageDraw.Draw(image)
        draw.ellipse((10, 10, 54, 54), fill="#2d89ef")
        draw.rectangle((30, 20, 36, 44), fill="white")
        draw.rectangle((24, 34, 42, 40), fill="white")
        return image

    def _update_loop(self):
        com_initialized = False
        temperature_reader = None

        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
                com_initialized = True
            except Exception:
                com_initialized = False

        try:
            temperature_reader = TemperatureReader()
            self.temperature_reader = temperature_reader

            while self.state.running:
                loop_start = time.perf_counter()

                cpu = psutil.cpu_percent(interval=0.5)
                memory = psutil.virtual_memory().percent
                temp_c, source = temperature_reader.get_temperature_reading()

                if source != self.temp_source:
                    self.temp_source = source
                    try:
                        self.icon.update_menu()
                    except Exception:
                        pass

                if temp_c is None:
                    temp_tip = "Temp: N/A"
                    temp_panel = "TEMP N/A"
                else:
                    if self.state.temp_unit == "F":
                        temp = (temp_c * 9 / 5) + 32
                        temp_tip = f"Temp: {temp:.1f} F"
                        temp_panel = f"TEMP {temp:.1f}F"
                    else:
                        temp_tip = f"Temp: {temp_c:.1f} C"
                        temp_panel = f"TEMP {temp_c:.1f}C"

                elapsed = max(0.0001, time.perf_counter() - loop_start)
                self.latest_fps = 1.0 / elapsed

                tip_parts = []
                panel_parts = []

                if self.state.show_cpu:
                    tip_parts.append(f"CPU: {cpu:.0f}%")
                    panel_parts.append(f"CPU {cpu:.0f}%")
                if self.state.show_ram:
                    tip_parts.append(f"RAM: {memory:.0f}%")
                    panel_parts.append(f"RAM {memory:.0f}%")
                if self.state.show_temp:
                    tip_parts.append(temp_tip)
                    panel_parts.append(temp_panel)
                if self.state.show_fps:
                    tip_parts.append(f"FPS: {self.latest_fps:.1f}")
                    panel_parts.append(f"FPS {self.latest_fps:.1f}")

                if tip_parts:
                    self.icon.title = " | ".join(tip_parts)
                    self.latest_text = "   ".join(panel_parts)
                else:
                    self.icon.title = "No metrics selected"
                    self.latest_text = "No metrics selected"
                time.sleep(max(0.5, self.state.refresh_seconds))
        finally:
            self.temperature_reader = None
            temperature_reader = None
            gc.collect()

            if com_initialized and pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _get_latest_text(self):
        return self.latest_text

    def _panel_is_visible(self):
        return self.state.show_panel

    def _get_panel_style(self):
        return {
            "font_color": self.state.font_color,
            "bg_color": self.state.bg_color,
            "transparent_bg": self.state.transparent_bg,
            "font_family": self.state.font_family,
        }

    def _set_celsius(self, icon=None, item=None):
        self.state.temp_unit = "C"
        self._persist_and_refresh_menu()

    def _set_fahrenheit(self, icon=None, item=None):
        self.state.temp_unit = "F"
        self._persist_and_refresh_menu()

    def _set_refresh(self, seconds: int):
        self.state.refresh_seconds = seconds
        self._persist_and_refresh_menu()

    def _set_font_family(self, family: str):
        self.state.font_family = family
        self._persist_and_refresh_menu()

    def _set_font_color(self, color: str):
        self.state.font_color = color
        self._persist_and_refresh_menu()

    def _set_bg_color(self, color: str):
        self.state.bg_color = color
        self._persist_and_refresh_menu()

    def _toggle_transparent_bg(self, icon=None, item=None):
        self.state.transparent_bg = not self.state.transparent_bg
        self._persist_and_refresh_menu()

    def _toggle_show_cpu(self, icon=None, item=None):
        self.state.show_cpu = not self.state.show_cpu
        self._persist_and_refresh_menu()

    def _toggle_show_ram(self, icon=None, item=None):
        self.state.show_ram = not self.state.show_ram
        self._persist_and_refresh_menu()

    def _toggle_show_temp(self, icon=None, item=None):
        self.state.show_temp = not self.state.show_temp
        self._persist_and_refresh_menu()

    def _toggle_show_fps(self, icon=None, item=None):
        self.state.show_fps = not self.state.show_fps
        self._persist_and_refresh_menu()

    def _toggle_panel(self, icon=None, item=None):
        self.state.show_panel = not self.state.show_panel
        self._persist_and_refresh_menu()

    def _toggle_startup(self, icon=None, item=None):
        if winreg is None:
            return
        if self._is_startup_enabled():
            self._disable_startup()
        else:
            self._enable_startup()

    def _is_startup_enabled(self) -> bool:
        if winreg is None:
            return False
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return bool(value)
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def _enable_startup(self):
        script_path = os.path.abspath(__file__)
        command = _build_launch_command(script_path)
        _set_startup_run_key(command)

    def _disable_startup(self):
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH)
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)

    def _quit(self, icon, item):
        self.state.running = False
        self._save_settings()
        self.panel.stop()
        icon.stop()


def _is_running_as_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _check_requirements():
    checks = [
        ("psutil", psutil is not None),
        ("pystray", pystray is not None),
        ("pillow", Image is not None and ImageDraw is not None),
        ("wmi", wmi is not None),
        ("pywin32", pythoncom is not None),
    ]
    return checks


def _check_data_sources():
    cpu_ok = False
    ram_ok = False
    temp_ok = False
    temp_source = "Unavailable"
    reader = None

    if psutil is not None:
        try:
            psutil.cpu_percent(interval=0.2)
            cpu_ok = True
        except Exception:
            cpu_ok = False

        try:
            _ = psutil.virtual_memory().percent
            ram_ok = True
        except Exception:
            ram_ok = False

    com_initialized = False
    if pythoncom is not None:
        try:
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            com_initialized = False

    try:
        reader = TemperatureReader()
        temp, source = reader.get_temperature_reading()
        temp_ok = temp is not None
        temp_source = source
    except Exception:
        temp_ok = False
        temp_source = "Unavailable"
    finally:
        reader = None
        gc.collect()

        if com_initialized and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    return cpu_ok, ram_ok, temp_ok, temp_source


def run_startup_checks() -> bool:
    print(f"[{APP_NAME}] Startup checks")

    admin = _is_running_as_admin()
    print(f"[{'OK' if admin else 'WARN'}] Admin privileges: {'Yes' if admin else 'No'}")

    reqs = _check_requirements()
    debug_log(f"Requirement checks: {reqs}")
    missing = [name for name, ok in reqs if not ok]
    for name, ok in reqs:
        print(f"[{'OK' if ok else 'FAIL'}] Requirement '{name}': {'installed' if ok else 'missing'}")

    if missing:
        print("[FAIL] Missing required packages. Install with: pip install -r requirements.txt")
        return False

    cpu_ok, ram_ok, temp_ok, temp_source = _check_data_sources()
    debug_log(f"Data checks: cpu_ok={cpu_ok}, ram_ok={ram_ok}, temp_ok={temp_ok}, source={temp_source}")
    print(f"[{'OK' if cpu_ok else 'FAIL'}] CPU data available")
    print(f"[{'OK' if ram_ok else 'FAIL'}] RAM data available")
    print(f"[{'OK' if temp_ok else 'WARN'}] Temp data available (source: {temp_source})")

    if not cpu_ok or not ram_ok:
        print("[FAIL] Required data sources (CPU/RAM) are unavailable.")
        return False

    if not temp_ok:
        print("[WARN] Temperature is unavailable right now. The app will run, but Temp will show N/A.")

    print("[OK] Startup checks complete.")
    return True


def _is_64bit_python() -> bool:
    return struct.calcsize("P") * 8 == 64


def _get_install_dir() -> str:
    if _is_64bit_python():
        return r"C:\Program Files\SystemMonitor"
    return r"C:\Program Files (x86)\SystemMonitor"


def _ensure_admin_for_action(action_name: str) -> bool:
    if _is_running_as_admin():
        return True
    print(f"[FAIL] {action_name} requires Administrator privileges.")
    return False


def _safe_remove_tree(path: str):
    if not os.path.isdir(path):
        return True
    try:
        shutil.rmtree(path, ignore_errors=False)
    except Exception:
        return False
    return not os.path.exists(path)


def _terminate_running_monitor_processes(install_dir: str) -> int:
    if psutil is None:
        return 0

    target_script = os.path.join(install_dir, INSTALL_SCRIPT_NAME).lower()
    install_dir_lower = install_dir.lower()
    current_pid = os.getpid()
    terminated = 0

    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            if pid <= 0 or pid == current_pid:
                continue

            exe = str(proc.info.get("exe") or "").lower()
            cmdline_parts = proc.info.get("cmdline") or []
            cmdline = " ".join(str(part) for part in cmdline_parts).lower()

            is_monitor = (
                target_script in cmdline
                or target_script in exe
                or install_dir_lower in cmdline
                or install_dir_lower in exe
            )
            if not is_monitor:
                continue

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
                proc.wait(timeout=3)
            terminated += 1
        except Exception:
            continue

    return terminated


def _schedule_self_cleanup(install_dir: str, pid_to_wait: int) -> bool:
    escaped_install_dir = install_dir.replace("'", "''")
    command = (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"$pidToWait={pid_to_wait}; "
        "while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 300 }; "
        "Start-Sleep -Seconds 1; "
        f"Remove-Item -LiteralPath '{escaped_install_dir}' -Recurse -Force -ErrorAction SilentlyContinue"
    )

    creation_flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creation_flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creation_flags |= subprocess.CREATE_NO_WINDOW

    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-Command",
                command,
            ],
            creationflags=creation_flags,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def _scheduled_task_command(pythonw_path: str, script_path: str) -> str:
    return _build_launch_command(script_path, pythonw_path)


def _install_app() -> int:
    if not _ensure_admin_for_action("Install"):
        return 1

    source_script = os.path.abspath(__file__)
    source_requirements = os.path.join(os.path.dirname(source_script), "requirements.txt")

    install_dir = _get_install_dir()
    install_script_path = os.path.join(install_dir, INSTALL_SCRIPT_NAME)
    install_requirements_path = os.path.join(install_dir, "requirements.txt")
    venv_dir = os.path.join(install_dir, ".venv")
    venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    venv_pythonw = os.path.join(venv_dir, "Scripts", "pythonw.exe")
    venv_pip = os.path.join(venv_dir, "Scripts", "pip.exe")

    try:
        os.makedirs(install_dir, exist_ok=True)

        shutil.copy2(source_script, install_script_path)
        if os.path.exists(source_requirements):
            shutil.copy2(source_requirements, install_requirements_path)

        if not os.path.exists(venv_python):
            print("[INFO] Creating virtual environment...")
            subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)

        print("[INFO] Installing Python requirements...")
        if os.path.exists(install_requirements_path):
            subprocess.run([venv_pip, "install", "-r", install_requirements_path], check=True)
        else:
            subprocess.run([venv_pip, "install", "psutil", "pystray", "pillow", "wmi", "pywin32"], check=True)

        task_command = _scheduled_task_command(venv_pythonw, install_script_path)
        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/F",
                "/SC",
                "ONLOGON",
                "/RL",
                "HIGHEST",
                "/TN",
                SCHEDULED_TASK_NAME,
                "/TR",
                task_command,
            ],
            check=True,
        )

        if winreg is not None:
            _set_startup_run_key(task_command)

        print("[OK] Installed successfully.")
        print(f"[OK] Installed path: {install_dir}")
        print(f"[OK] Startup task created: {SCHEDULED_TASK_NAME}")

        try:
            subprocess.Popen([venv_pythonw, install_script_path])
            print("[OK] Monitor started.")
        except Exception as exc:
            print(f"[WARN] Installed, but could not auto-start now: {exc}")

        return 0
    except Exception as exc:
        print(f"[FAIL] Install failed: {exc}")
        return 1


def _uninstall_app() -> int:
    if not _ensure_admin_for_action("Uninstall"):
        return 1

    try:
        subprocess.run(["schtasks", "/End", "/TN", SCHEDULED_TASK_NAME], check=False)
        subprocess.run(["schtasks", "/Delete", "/TN", SCHEDULED_TASK_NAME, "/F"], check=False)

        if winreg is not None:
            try:
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH)
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
                finally:
                    winreg.CloseKey(key)
            except OSError:
                pass

        install_dir = _get_install_dir()
        terminated_count = _terminate_running_monitor_processes(install_dir)
        if terminated_count > 0:
            print(f"[OK] Stopped running monitor instances: {terminated_count}")

        current_script = os.path.abspath(__file__).lower()
        installed_script = os.path.join(install_dir, INSTALL_SCRIPT_NAME).lower()
        if current_script == installed_script:
            scheduled = _schedule_self_cleanup(install_dir, os.getpid())
            if scheduled:
                print("[OK] Uninstall cleanup scheduled.")
                print(f"[OK] Install directory will be removed after exit: {install_dir}")
                return 0
            print("[FAIL] Could not schedule cleanup from installed location.")
            return 1

        removed = _safe_remove_tree(install_dir)
        if removed:
            print("[OK] Uninstall complete.")
            print(f"[OK] Removed: {install_dir}")
            return 0

        print(f"[FAIL] Could not fully remove install directory: {install_dir}")
        return 1
    except Exception as exc:
        print(f"[FAIL] Uninstall failed: {exc}")
        return 1


def _parse_cli_args(argv):
    options = {
        "debug": False,
        "install": False,
        "uninstall": False,
    }
    for arg in argv:
        normalized = arg.strip().lower()
        if normalized in ["-debug", "--debug"]:
            options["debug"] = True
        elif normalized in ["-install", "--install"]:
            options["install"] = True
        elif normalized in ["-uninstall", "--uninstall"]:
            options["uninstall"] = True
    return options

if __name__ == "__main__":
    cli_options = _parse_cli_args(sys.argv[1:])
    DEBUG_MODE = cli_options["debug"]

    if DEBUG_MODE:
        print("[DEBUG] Debug mode enabled")
        print(f"[DEBUG] Args: {sys.argv[1:]}")

    if cli_options["install"] and cli_options["uninstall"]:
        print("[FAIL] Choose either --install or --uninstall, not both.")
        sys.exit(1)

    if cli_options["install"]:
        sys.exit(_install_app())

    if cli_options["uninstall"]:
        sys.exit(_uninstall_app())

    if run_startup_checks():
        TrayMonitor().run()





























