import psutil

try:
    import wmi
except ImportError:
    wmi = None


def dump_psutil():
    print('=== psutil.sensors_temperatures() ===')
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
    except Exception as e:
        print(f'error: {e}')
        return

    if not temps:
        print('no temperature entries')
        return

    for group, entries in temps.items():
        print(f'[{group}]')
        for entry in entries:
            print(f'  label={entry.label!r} current={entry.current} high={entry.high} critical={entry.critical}')


def dump_wmi(namespace):
    print(f'=== WMI namespace: {namespace} ===')
    if wmi is None:
        print('wmi module not installed')
        return

    try:
        client = wmi.WMI(namespace=namespace)
    except Exception as e:
        print(f'connect error: {e}')
        return

    try:
        sensors = client.Sensor()
    except Exception as e:
        print(f'Sensor() error: {e}')
        return

    if not sensors:
        print('no Sensor rows')
        return

    for s in sensors:
        print(
            f"  name={getattr(s, 'Name', None)!r} type={getattr(s, 'SensorType', None)!r} "
            f"value={getattr(s, 'Value', None)!r} id={getattr(s, 'Identifier', None)!r}"
        )


def dump_acpi():
    print('=== WMI ACPI root\\wmi MSAcpi_ThermalZoneTemperature ===')
    if wmi is None:
        print('wmi module not installed')
        return

    try:
        client = wmi.WMI(namespace='root\\wmi')
        zones = client.MSAcpi_ThermalZoneTemperature()
    except Exception as e:
        print(f'error: {e}')
        return

    if not zones:
        print('no thermal zones')
        return

    for z in zones:
        raw = getattr(z, 'CurrentTemperature', None)
        if raw is None:
            continue
        c = (raw / 10.0) - 273.15
        print(f'  zone={getattr(z, "InstanceName", None)!r} raw={raw} celsius={c:.2f}')


if __name__ == '__main__':
    dump_psutil()
    dump_wmi('root\\LibreHardwareMonitor')
    dump_wmi('root\\OpenHardwareMonitor')
    dump_acpi()
