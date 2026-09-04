"""How a discovered device is described in the UI.

Shared, so the Monitor and Configurator describe a device identically.
"""

#: Statuses that mean the OS is happy to drive the device. Backends that cannot
#: report OS state (USRP) send nothing, which is taken as healthy.
HEALTHY_STATUSES = {"OK", "AVAILABLE", "HEALTHY"}


def device_is_healthy(device) -> bool:
    """Whether the OS considers this device usable.

    A device whose driver failed to load is still discovered and still listed.
    """
    status = (device or {}).get("status")
    if not status:
        return True
    return str(status).upper() in HEALTHY_STATUSES


def device_details(device) -> str:
    """The identifying line shown under a device's name.

    Only what the device actually reports; not every device has a serial.
    """
    device = device or {}
    details = [device.get("device_type", device.get("type", "Unknown"))]

    if device.get("model"):
        details.append(str(device["model"]))
    if device.get("serial"):
        details.append(f"S/N {device['serial']}")
    if device.get("manufacturer") and device["manufacturer"] != "Unknown":
        details.append(str(device["manufacturer"]))
    if not device_is_healthy(device):
        details.append(f"[{device.get('status')}]")

    return "  ·  ".join(details)


def device_health_warning(device) -> str:
    """A message explaining an unusable device, or "" when it is fine.

    Routed through the shared issue catalogue so wording stays consistent.
    """
    if device_is_healthy(device):
        return ""

    from bioview_common import describe_failure

    device = device or {}
    name = device.get("name", "Device")
    status = device.get("status")
    base = (
        f"{name} is attached but Windows reports it as '{status}' -- its driver "
        "is not working, so acquisition cannot open it."
    )
    explained = describe_failure(
        f"device driver status {status}", include_original=False
    )
    if explained and explained.lower() not in base.lower():
        return f"{base} {explained}"
    return base
