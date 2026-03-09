param(
    [switch]$Debug,
    [switch]$Install,
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-DebugLog([string]$m) { if ($Debug) { Write-Host "[DEBUG] $m" } }

function Get-FlagArgs {
    $a = @()
    if ($Debug) { $a += '-Debug' }
    if ($Install) { $a += '-Install' }
    if ($Uninstall) { $a += '-Uninstall' }
    return $a
}

# Ensure WinForms runs in Windows PowerShell Desktop + STA for tray/panel reliability.
if (-not $env:SYSTEMMONITOR_RELAUNCHED) {
    $needDesktop = $PSVersionTable.PSEdition -ne 'Desktop'
    $needSta = [Threading.Thread]::CurrentThread.ApartmentState -ne 'STA'
    if ($needDesktop -or $needSta) {
        $winps = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        if (Test-Path -LiteralPath $winps) {
            $self = if ($PSCommandPath) { $PSCommandPath } else { '.\SystemMonitor.ps1' }
            $args = @('-NoProfile','-ExecutionPolicy','Bypass','-STA','-File',$self) + (Get-FlagArgs)
            Start-Process -FilePath $winps -ArgumentList $args -WorkingDirectory (Get-Location) -Environment @{ SYSTEMMONITOR_RELAUNCHED = '1' }
            exit 0
        }
    }
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
} catch {
    Write-Host '[FAIL] Could not load System.Windows.Forms/System.Drawing.'
    exit 1
}

$script:APP_NAME = 'Laptop Monitor'
$script:RUN_KEY_PATH = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$script:SCHEDULED_TASK_NAME = 'SystemMonitor'
$script:INSTALL_SCRIPT_NAME = 'SystemMonitor.ps1'
$script:CONFIG_FILE_NAME = 'tray_monitor_settings.json'

$script:FONT_COLORS = [ordered]@{
    White  = '#f3f3f3'
    Black  = '#000000'
    Green  = '#22c55e'
    Cyan   = '#22d3ee'
    Yellow = '#facc15'
    Red    = '#ef4444'
}

$script:BG_COLORS = [ordered]@{
    Dark   = '#111111'
    Slate  = '#1e293b'
    Navy   = '#0f172a'
    Maroon = '#3f1d1d'
    Forest = '#1b4332'
    Light  = '#f5f5f5'
}

$script:FONT_FAMILIES = @('Segoe UI','Consolas','Arial')

function Get-ScriptPath {
    if ($PSCommandPath) { return (Resolve-Path $PSCommandPath).Path }
    return (Resolve-Path '.\SystemMonitor.ps1').Path
}

function Get-InstallDir {
    if ([Environment]::Is64BitOperatingSystem) { return 'C:\Program Files\SystemMonitor' }
    return 'C:\Program Files (x86)\SystemMonitor'
}

function Get-ConfigPath([string]$baseDir = $null) {
    if (-not $baseDir) { $baseDir = Split-Path -Parent (Get-ScriptPath) }
    return Join-Path $baseDir $script:CONFIG_FILE_NAME
}

function New-DefaultState {
    return [ordered]@{
        temp_unit       = 'C'
        refresh_seconds = 2
        show_panel      = $true
        font_color      = '#f3f3f3'
        bg_color        = '#111111'
        transparent_bg  = $true
        font_family     = 'Segoe UI'
        show_cpu        = $true
        show_ram        = $true
        show_temp       = $true
        show_fps        = $false
    }
}

function Load-State {
    $state = New-DefaultState
    $path = Get-ConfigPath
    if (-not (Test-Path -LiteralPath $path)) { return $state }

    try {
        $loaded = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        if ($loaded.temp_unit -in @('C','F')) { $state.temp_unit = [string]$loaded.temp_unit }
        if ($loaded.refresh_seconds -in @(1,2,5)) { $state.refresh_seconds = [int]$loaded.refresh_seconds }
        foreach ($k in @('show_panel','transparent_bg','show_cpu','show_ram','show_temp','show_fps')) {
            if ($null -ne $loaded.$k -and $loaded.$k -is [bool]) { $state[$k] = [bool]$loaded.$k }
        }
        if ($loaded.font_color) { $state.font_color = [string]$loaded.font_color }
        if ($loaded.bg_color) { $state.bg_color = [string]$loaded.bg_color }
        if ($loaded.font_family -in $script:FONT_FAMILIES) { $state.font_family = [string]$loaded.font_family }
    } catch {
        Write-DebugLog "Load-State failed: $($_.Exception.Message)"
    }
    return $state
}

function Save-State([hashtable]$state) {
    try { $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Get-ConfigPath) -Encoding UTF8 }
    catch { Write-DebugLog "Save-State failed: $($_.Exception.Message)" }
}

function Convert-HtmlColor([string]$hex) {
    try { return [Drawing.ColorTranslator]::FromHtml($hex) }
    catch { return [Drawing.Color]::White }
}

function Is-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = [Security.Principal.WindowsPrincipal]::new($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-LaunchCommand([string]$scriptPath) {
    return "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File `"$scriptPath`""
}

function Enable-Startup([string]$scriptPath) {
    $cmd = Get-LaunchCommand $scriptPath
    New-ItemProperty -Path $script:RUN_KEY_PATH -Name $script:APP_NAME -PropertyType String -Value $cmd -Force | Out-Null
}

function Disable-Startup {
    Remove-ItemProperty -Path $script:RUN_KEY_PATH -Name $script:APP_NAME -ErrorAction SilentlyContinue
}

function Get-StartupEnabled {
    try {
        $v = (Get-ItemProperty -Path $script:RUN_KEY_PATH -Name $script:APP_NAME -ErrorAction Stop).$($script:APP_NAME)
        return [bool]$v
    } catch { return $false }
}

function Try-GetShortPath([string]$path) {
    try {
        $result = cmd /c "for %I in (\"$path\") do @echo %~sI"
        if ($LASTEXITCODE -eq 0 -and $result) {
            $short = ($result | Select-Object -First 1).Trim()
            if ($short) { return $short }
        }
    } catch {}
    return $null
}

function Register-LogonTask([string]$installScript) {
    # Prefer ScheduledTasks cmdlets first, then fallback to schtasks.exe.
    $taskArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File `"$installScript`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArgs
    $trigger = New-ScheduledTaskTrigger -AtLogOn

    $userCandidates = @(
        [Security.Principal.WindowsIdentity]::GetCurrent().Name,
        "$($env:USERDOMAIN)\$($env:USERNAME)",
        "$($env:COMPUTERNAME)\$($env:USERNAME)",
        $env:USERNAME
    ) | Where-Object { $_ } | Select-Object -Unique

    foreach ($userId in $userCandidates) {
        try {
            $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest
            Register-ScheduledTask -TaskName $script:SCHEDULED_TASK_NAME -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
            return $true
        } catch { Write-DebugLog "Register-ScheduledTask failed for '$userId': $($_.Exception.Message)" }
    }

    try {
        $shortScript = Try-GetShortPath $installScript
        if (-not $shortScript) { $shortScript = $installScript }
        $scriptArg = if ($shortScript -match '\s') { "`"$shortScript`"" } else { $shortScript }
        $tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File $scriptArg"
        $proc = Start-Process -FilePath 'schtasks.exe' -ArgumentList @('/Create','/F','/SC','ONLOGON','/RL','HIGHEST','/TN',$script:SCHEDULED_TASK_NAME,'/TR',$tr) -Wait -PassThru -NoNewWindow
        if ($proc.ExitCode -eq 0) { return $true }
    } catch {}
    return $false
}

function Remove-LogonTask {
    try {
        Stop-ScheduledTask -TaskName $script:SCHEDULED_TASK_NAME -ErrorAction SilentlyContinue | Out-Null
        Unregister-ScheduledTask -TaskName $script:SCHEDULED_TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    } catch {
        & schtasks /End /TN $script:SCHEDULED_TASK_NAME | Out-Null
        & schtasks /Delete /TN $script:SCHEDULED_TASK_NAME /F | Out-Null
    }
}

function Ensure-Admin([string]$name) {
    if (Is-Admin) { return $true }
    Write-Host "[FAIL] $name requires Administrator privileges."
    return $false
}

function Install-App {
    if (-not (Ensure-Admin 'Install')) { return 1 }
    $src = Get-ScriptPath
    $dir = Get-InstallDir
    $dst = Join-Path $dir $script:INSTALL_SCRIPT_NAME

    try {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst -Force

        # Seed default settings so first run is visible and predictable.
        $installState = New-DefaultState
        $installState.show_panel = $true
        $installState | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Get-ConfigPath $dir) -Encoding UTF8

        $taskCreated = Register-LogonTask $dst
        if (-not $taskCreated) { throw 'Scheduled task could not be created.' }

        Enable-Startup $dst

        $startedNow = $false
        try {
            Start-ScheduledTask -TaskName $script:SCHEDULED_TASK_NAME -ErrorAction Stop
            $startedNow = $true
        } catch {
            Write-DebugLog "Start-ScheduledTask failed: $($_.Exception.Message)"
        }

        if (-not $startedNow) {
            Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-STA','-File',$dst) | Out-Null
            $startedNow = $true
        }

        Write-Host '[OK] Installed successfully.'
        Write-Host "[OK] Installed path: $dir"
        Write-Host "[OK] Startup task created: $($script:SCHEDULED_TASK_NAME)"
        Write-Host '[OK] Monitor started.'
        return 0
    } catch {
        Write-Host "[FAIL] Install failed: $($_.Exception.Message)"
        return 1
    }
}

function Uninstall-App {
    if (-not (Ensure-Admin 'Uninstall')) { return 1 }
    try {
        Remove-LogonTask
        Disable-Startup
        $dir = Get-InstallDir
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        Write-Host '[OK] Uninstall complete.'
        Write-Host "[OK] Removed: $dir"
        return 0
    } catch {
        Write-Host "[FAIL] Uninstall failed: $($_.Exception.Message)"
        return 1
    }
}

function Get-Temp {
    foreach ($ns in @('root/LibreHardwareMonitor','root/OpenHardwareMonitor')) {
        try {
            $sensors = Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop | Where-Object { "$($_.SensorType)" -match 'Temp|Temperature' }
            $cpuTemps = @(); $anyTemps = @()
            foreach ($s in $sensors) {
                $t = $null; try { $t = [double]$s.Value } catch { continue }
                if ($t -lt -20 -or $t -gt 130) { continue }
                $anyTemps += $t
                $name = ("{0} {1}" -f $s.Name, $s.Identifier).ToLowerInvariant()
                if ($name -match 'cpu|package|core') { $cpuTemps += $t }
            }
            if ($cpuTemps.Count -gt 0) { return @{ value = ($cpuTemps | Measure-Object -Maximum).Maximum; source = 'Libre/OpenHardwareMonitor' } }
            if ($anyTemps.Count -gt 0) { return @{ value = ($anyTemps | Measure-Object -Maximum).Maximum; source = 'Libre/OpenHardwareMonitor' } }
        } catch {}
    }

    try {
        $zones = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop
        $vals = @($zones | ForEach-Object { if ($_.CurrentTemperature) { $c = ([double]$_.CurrentTemperature/10)-273.15; if ($c -ge -20 -and $c -le 130) { $c } } })
        if ($vals.Count -gt 0) { return @{ value = ($vals | Measure-Object -Maximum).Maximum; source = 'ACPI' } }
    } catch {}

    return @{ value = $null; source = 'Unavailable' }
}

function Run-StartupChecks {
    Write-Host "[$($script:APP_NAME)] Startup checks"
    Write-Host ("[{0}] Admin privileges: {1}" -f ($(if (Is-Admin) { 'OK' } else { 'WARN' }), $(if (Is-Admin) { 'Yes' } else { 'No' })))

    try {
        $c = [Diagnostics.PerformanceCounter]::new('Processor','% Processor Time','_Total')
        $null = $c.NextValue(); Start-Sleep -Milliseconds 200; $null = $c.NextValue()
        Write-Host '[OK] CPU data available'
    } catch {
        Write-Host '[FAIL] CPU data unavailable'
        return $false
    }

    $ramOk = $false
    try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop | Out-Null; $ramOk = $true }
    catch {
        try { $r=[Diagnostics.PerformanceCounter]::new('Memory','% Committed Bytes In Use'); $null=$r.NextValue(); $ramOk=$true } catch { $ramOk=$false }
    }
    if ($ramOk) { Write-Host '[OK] RAM data available' } else { Write-Host '[FAIL] RAM data unavailable'; return $false }

    $t = Get-Temp
    if ($null -eq $t.value) { Write-Host "[WARN] Temp data unavailable (source: $($t.source))" }
    else { Write-Host "[OK] Temp data available (source: $($t.source))" }

    Write-Host '[OK] Startup checks complete.'
    return $true
}

function Start-Monitor {
    $script:state = Load-State
    $script:tempSource = 'Initializing'
    $script:latestText = 'CPU --%   RAM --%   TEMP --'
    $script:latestFps = 0.0

    $cpuCounter = [Diagnostics.PerformanceCounter]::new('Processor','% Processor Time','_Total')
    $null = $cpuCounter.NextValue()
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()

    $notify = [Windows.Forms.NotifyIcon]::new()
    $notify.Visible = $true
    $notify.Text = $script:APP_NAME

    $bmp = [Drawing.Bitmap]::new(64,64)
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.Clear([Drawing.Color]::FromArgb(32,37,43))
    $g.FillEllipse([Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(45,137,239)),10,10,44,44)
    $g.FillRectangle([Drawing.Brushes]::White,30,20,6,24)
    $g.FillRectangle([Drawing.Brushes]::White,24,34,18,6)
    $notify.Icon = [Drawing.Icon]::FromHandle($bmp.GetHicon())
    $g.Dispose(); $bmp.Dispose()

    $panel = [Windows.Forms.Form]::new()
    $panel.FormBorderStyle = 'None'
    $panel.ShowInTaskbar = $false
    $panel.TopMost = $true
    $panel.AutoSize = $true
    $panel.AutoSizeMode = [Windows.Forms.AutoSizeMode]::GrowAndShrink

    $label = [Windows.Forms.Label]::new()
    $label.AutoSize = $true
    $label.Padding = [Windows.Forms.Padding]::new(10,6,10,6)
    $panel.Controls.Add($label)

    function Place-Panel([Windows.Forms.Form]$f) {
        $w = [Windows.Forms.Screen]::PrimaryScreen.WorkingArea
        $f.PerformLayout()
        $f.Location = [Drawing.Point]::new($w.Right - $f.Width - 18, $w.Bottom - $f.Height - 18)
    }

    function Apply-PanelStyle {
        $fontColor = Convert-HtmlColor $script:state.font_color
        $bgColor = Convert-HtmlColor $script:state.bg_color
        $label.ForeColor = $fontColor
        $label.BackColor = $bgColor
        $panel.BackColor = $bgColor

        $fam = if ($script:state.font_family -in $script:FONT_FAMILIES) { $script:state.font_family } else { 'Segoe UI' }
        try { $label.Font = [Drawing.Font]::new($fam,9,[Drawing.FontStyle]::Bold) }
        catch { $label.Font = [Drawing.Font]::new('Segoe UI',9,[Drawing.FontStyle]::Bold) }

        if ($script:state.transparent_bg) { $panel.TransparencyKey = $bgColor }
        else { $panel.TransparencyKey = [Drawing.Color]::Empty }
    }

    function Persist-And-RefreshMenu {
        Save-State $script:state
        Build-Menu
    }

    function Build-Menu {
        $menu = [Windows.Forms.ContextMenuStrip]::new()

        $src = [Windows.Forms.ToolStripMenuItem]::new("Temp Source: $($script:tempSource)")
        $src.Enabled = $false
        $menu.Items.Add($src) | Out-Null
        $menu.Items.Add('-') | Out-Null

        $display = [Windows.Forms.ToolStripMenuItem]::new('Display Metrics')
        $mCpu=[Windows.Forms.ToolStripMenuItem]::new('Show CPU'); $mCpu.Checked=[bool]$script:state.show_cpu; $mCpu.add_Click({$script:state.show_cpu=-not [bool]$script:state.show_cpu; Persist-And-RefreshMenu}); $display.DropDownItems.Add($mCpu)|Out-Null
        $mRam=[Windows.Forms.ToolStripMenuItem]::new('Show RAM'); $mRam.Checked=[bool]$script:state.show_ram; $mRam.add_Click({$script:state.show_ram=-not [bool]$script:state.show_ram; Persist-And-RefreshMenu}); $display.DropDownItems.Add($mRam)|Out-Null
        $mTmp=[Windows.Forms.ToolStripMenuItem]::new('Show Temp'); $mTmp.Checked=[bool]$script:state.show_temp; $mTmp.add_Click({$script:state.show_temp=-not [bool]$script:state.show_temp; Persist-And-RefreshMenu}); $display.DropDownItems.Add($mTmp)|Out-Null
        $mFps=[Windows.Forms.ToolStripMenuItem]::new('Show FPS'); $mFps.Checked=[bool]$script:state.show_fps; $mFps.add_Click({$script:state.show_fps=-not [bool]$script:state.show_fps; Persist-And-RefreshMenu}); $display.DropDownItems.Add($mFps)|Out-Null
        $menu.Items.Add($display)|Out-Null

        $units=[Windows.Forms.ToolStripMenuItem]::new('Temperature Unit')
        $cItem=[Windows.Forms.ToolStripMenuItem]::new('Celsius'); $cItem.Checked=($script:state.temp_unit -eq 'C'); $cItem.add_Click({$script:state.temp_unit='C'; Persist-And-RefreshMenu}); $units.DropDownItems.Add($cItem)|Out-Null
        $fItem=[Windows.Forms.ToolStripMenuItem]::new('Fahrenheit'); $fItem.Checked=($script:state.temp_unit -eq 'F'); $fItem.add_Click({$script:state.temp_unit='F'; Persist-And-RefreshMenu}); $units.DropDownItems.Add($fItem)|Out-Null
        $menu.Items.Add($units)|Out-Null

        $refresh=[Windows.Forms.ToolStripMenuItem]::new('Refresh')
        foreach($sec in @(1,2,5)){ $it=[Windows.Forms.ToolStripMenuItem]::new("$sec sec"); $it.Checked=([int]$script:state.refresh_seconds -eq $sec); $v=$sec; $it.add_Click({$script:state.refresh_seconds=$v; $timer.Interval=[int]$script:state.refresh_seconds*1000; Persist-And-RefreshMenu}); $refresh.DropDownItems.Add($it)|Out-Null }
        $menu.Items.Add($refresh)|Out-Null

        $fams=[Windows.Forms.ToolStripMenuItem]::new('Font Family')
        foreach($family in $script:FONT_FAMILIES){ $it=[Windows.Forms.ToolStripMenuItem]::new($family); $it.Checked=($script:state.font_family -eq $family); $v=$family; $it.add_Click({$script:state.font_family=$v; Apply-PanelStyle; Persist-And-RefreshMenu}); $fams.DropDownItems.Add($it)|Out-Null }
        $menu.Items.Add($fams)|Out-Null

        $fc=[Windows.Forms.ToolStripMenuItem]::new('Font Color')
        foreach($e in $script:FONT_COLORS.GetEnumerator()){ $it=[Windows.Forms.ToolStripMenuItem]::new($e.Key); $it.Checked=($script:state.font_color.ToLowerInvariant() -eq $e.Value.ToLowerInvariant()); $v=$e.Value; $it.add_Click({$script:state.font_color=$v; Apply-PanelStyle; Persist-And-RefreshMenu}); $fc.DropDownItems.Add($it)|Out-Null }
        $menu.Items.Add($fc)|Out-Null

        $bc=[Windows.Forms.ToolStripMenuItem]::new('Background Color')
        foreach($e in $script:BG_COLORS.GetEnumerator()){ $it=[Windows.Forms.ToolStripMenuItem]::new($e.Key); $it.Checked=($script:state.bg_color.ToLowerInvariant() -eq $e.Value.ToLowerInvariant()); $v=$e.Value; $it.add_Click({$script:state.bg_color=$v; Apply-PanelStyle; Persist-And-RefreshMenu}); $bc.DropDownItems.Add($it)|Out-Null }
        $menu.Items.Add($bc)|Out-Null

        $trans=[Windows.Forms.ToolStripMenuItem]::new('Transparent Background'); $trans.Checked=[bool]$script:state.transparent_bg; $trans.add_Click({$script:state.transparent_bg=-not [bool]$script:state.transparent_bg; Apply-PanelStyle; Persist-And-RefreshMenu}); $menu.Items.Add($trans)|Out-Null
        $showPanel=[Windows.Forms.ToolStripMenuItem]::new('Show Mini Panel'); $showPanel.Checked=[bool]$script:state.show_panel; $showPanel.add_Click({$script:state.show_panel=-not [bool]$script:state.show_panel; if($script:state.show_panel){$panel.Show()}else{$panel.Hide()}; Persist-And-RefreshMenu}); $menu.Items.Add($showPanel)|Out-Null

        $startup=[Windows.Forms.ToolStripMenuItem]::new('Start With Windows'); $startup.Checked=Get-StartupEnabled; $startup.add_Click({ if(Get-StartupEnabled){Disable-Startup}else{Enable-Startup (Get-ScriptPath)}; Build-Menu }); $menu.Items.Add($startup)|Out-Null

        $menu.Items.Add('-')|Out-Null
        $quit=[Windows.Forms.ToolStripMenuItem]::new('Quit')
        $quit.add_Click({ Save-State $script:state; $timer.Stop(); $panel.Hide(); $notify.Visible=$false; $notify.Dispose(); [Windows.Forms.Application]::ExitThread() })
        $menu.Items.Add($quit)|Out-Null

        # Dispose previous menu instance before swapping to avoid object buildup.
        if ($notify.ContextMenuStrip) {
            $notify.ContextMenuStrip.Dispose()
        }
        $notify.ContextMenuStrip = $menu
    }

    $timer = [Windows.Forms.Timer]::new()
    $timer.Interval = [int]$script:state.refresh_seconds * 1000
    $timer.add_Tick({
        try { $cpu=[double]$cpuCounter.NextValue() } catch { $cpu=0 }
        try { $os=Get-CimInstance Win32_OperatingSystem; $ram=(1-([double]$os.FreePhysicalMemory/[double]$os.TotalVisibleMemorySize))*100 } catch { $ram=0 }

        $tmp = Get-Temp
        $sourceChanged = $script:tempSource -ne $tmp.source
        $script:tempSource = $tmp.source
        if ($null -eq $tmp.value) { $tempTip='Temp: N/A'; $tempPanel='TEMP N/A' }
        else {
            if ($script:state.temp_unit -eq 'F') { $f=(($tmp.value*9)/5)+32; $tempTip=('Temp: {0:N1} F' -f $f); $tempPanel=('TEMP {0:N1}F' -f $f) }
            else { $tempTip=('Temp: {0:N1} C' -f $tmp.value); $tempPanel=('TEMP {0:N1}C' -f $tmp.value) }
        }

        $script:latestFps = 1.0 / [Math]::Max(0.0001,$stopwatch.Elapsed.TotalSeconds)
        $stopwatch.Restart()

        $tip=@(); $panelParts=@()
        if($script:state.show_cpu){$tip+=('CPU: {0:N0}%' -f $cpu); $panelParts+=('CPU {0:N0}%' -f $cpu)}
        if($script:state.show_ram){$tip+=('RAM: {0:N0}%' -f $ram); $panelParts+=('RAM {0:N0}%' -f $ram)}
        if($script:state.show_temp){$tip+=$tempTip; $panelParts+=$tempPanel}
        if($script:state.show_fps){$tip+=('FPS: {0:N1}' -f $script:latestFps); $panelParts+=('FPS {0:N1}' -f $script:latestFps)}

        if($tip.Count -gt 0){
            $title=[string]::Join(' | ', $tip)
            if($title.Length -gt 63){$title=$title.Substring(0,63)}
            $notify.Text=$title
            $script:latestText=[string]::Join('   ', $panelParts)
        } else {
            $notify.Text='No metrics selected'
            $script:latestText='No metrics selected'
        }

        $label.Text = $script:latestText
        $panel.ClientSize = $label.PreferredSize
        Apply-PanelStyle

        if($script:state.show_panel){ if(-not $panel.Visible){$panel.Show()}; Place-Panel $panel }
        else { if($panel.Visible){$panel.Hide()} }

        if ($sourceChanged) {
            Build-Menu
        }
    })

    Apply-PanelStyle
    Build-Menu
    if($script:state.show_panel){ $panel.Show(); Place-Panel $panel }
    $timer.Start()
    [Windows.Forms.Application]::Run()
}

if ($Install -and $Uninstall) { Write-Host '[FAIL] Choose either -Install or -Uninstall, not both.'; exit 1 }
if ($Install) { exit (Install-App) }
if ($Uninstall) { exit (Uninstall-App) }
if (Run-StartupChecks) { Start-Monitor }





