# SystemMonitor - CPU, RAM, Temp, FPS

![SystemMonitor Screenshot](https://github.com/Nyckosan/SystemMonitor/blob/main/SystemMonitor_Screenshot.png?raw=true)



System tray/mini-panel monitor for:
- CPU usage (%)
- RAM usage (%)
- Temperature (C/F)
- FPS-style refresh metric

## Versions

This repo includes two versions:
- `SystemMonitor.ps1` (recommended): native PowerShell version, no Python required
- `SystemMonitor.py`: original Python version

## PowerShell Version (Recommended)

### Run from repo

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File .\SystemMonitor.ps1
```

### Install

`-Install` is designed to:
- install to `C:\Program Files\SystemMonitor` (admin required)
- configure startup (scheduled task + Run key)
- launch the monitor immediately

```powershell
.\SystemMonitor.ps1 -Install
```

### Uninstall

```powershell
.\SystemMonitor.ps1 -Uninstall
```

### Debug mode

```powershell
.\SystemMonitor.ps1 -Debug
.\SystemMonitor.ps1 -Install -Debug
```

### PowerShell CLI options

- `-Install`: install + startup registration + start now (Administrator required)
- `-Uninstall`: remove install and startup registration (Administrator required)
- `-Debug`: print debug logs

### Tray menu features

- Display Metrics: Show CPU, Show RAM, Show Temp, Show FPS
- Temperature Unit: Celsius/Fahrenheit
- Refresh: 1 sec / 2 sec / 5 sec
- Font Family
- Font Color
- Background Color
- Transparent Background
- Show Mini Panel
- Start With Windows
- Quit

## Python Version

The Python version is available in `SystemMonitor.py`.

### Run

```powershell
python SystemMonitor.py
```

### Python CLI options

- `--install`: install to Program Files, configure startup task/Run key, and start monitor (Administrator required)
- `--uninstall`: remove installed files and startup registration (Administrator required)
- `--debug`: enable debug logging

Examples:

```powershell
python SystemMonitor.py --debug
python SystemMonitor.py --install
python SystemMonitor.py --uninstall
```

## Temperature Note

Windows does not always expose direct CPU package temp. The app tries:
1. `root\\LibreHardwareMonitor`
2. `root\\OpenHardwareMonitor`
3. `root\\wmi` ACPI thermal zones

For best CPU temperature readings, keep LibreHardwareMonitor running.



