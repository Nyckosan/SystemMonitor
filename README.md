# Laptop Taskbar Monitor (Windows)

Small tray/taskbar monitor for:
- CPU usage (%)
- Memory usage (%)
- Temperature (C/F)

It runs in the system tray and also shows an always-visible mini panel near the taskbar (so you can read values without hovering).

Right-click the tray icon for a small menu with:
- Temperature unit (Celsius/Fahrenheit)
- Refresh interval (1s/2s/5s)
- Show Mini Panel (on/off)
- Start with Windows toggle
- Quit

## 1) Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2) Run

```powershell
python SystemMonitor.py
```

## Temperature note (important)

Windows does not always expose CPU temp directly.
This app tries these sources in order:
1. LibreHardwareMonitor WMI (`root\\LibreHardwareMonitor`)
2. OpenHardwareMonitor WMI (`root\\OpenHardwareMonitor`)
3. ACPI thermal zone fallback (`root\\wmi`)

For best CPU temperature readings, run LibreHardwareMonitor in the background.

## Optional: run silently

```powershell
pythonw SystemMonitor.py
```

