"""Read-only AppleSMC battery keys (cell voltages, lifetime record).

macOS 27.0 removed BatteryData.CellVoltage and BatteryData.LifetimeData from
ioreg. The same values are still published as SMC keys. Only the fixed key
whitelist below is ever read (SMC commands 9 = key info, 5 = read); the
write command is never sent.

FIXTURE-VERIFIED on the target M4 Pro (tests/fixtures/NOTES.md (g)):
values are little-endian; BLIC/BLID/BLPX/BLTX/BLTM equal the July ioreg
LifetimeData exactly, BLTO grew by exactly the elapsed wall-clock hours,
BC1V+BC2V+BC3V matches pack voltage B0AV within 1 mV.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import struct
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

CELL_KEYS = ("BC1V", "BC2V", "BC3V", "BC4V")
LIFETIME_KEYS = {
    "BLTM": "lifetime_temp_min",       # deg C
    "BLTX": "lifetime_temp_max",       # deg C
    "BLTA": "lifetime_temp_avg",       # 0.1 deg C
    "BLTO": "operating_time_hours",    # gauge powered hours
    "BLIC": "lifetime_max_charge_ma",
    "BLID": "lifetime_max_discharge_ma",
    "BLPX": "lifetime_pack_max_mv",
    "BLPM": "lifetime_pack_min_mv",
    "BLCX": "lifetime_cell_max_mv",
    "BLCM": "lifetime_cell_min_mv",
}
KEYS = CELL_KEYS + tuple(LIFETIME_KEYS)

_FORMATS = {"ui8 ": "<B", "ui16": "<H", "ui32": "<I",
            "si8 ": "<b", "si16": "<h", "si32": "<i", "flt ": "<f"}

_CMD_READ = 5
_CMD_KEY_INFO = 9
_SELECTOR = 2  # kSMCHandleYPCEvent


class _Vers(ctypes.Structure):
    _fields_ = [("major", ctypes.c_ubyte), ("minor", ctypes.c_ubyte),
                ("build", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte),
                ("release", ctypes.c_uint16)]


class _PLimit(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16),
                ("cpu", ctypes.c_uint32), ("gpu", ctypes.c_uint32),
                ("mem", ctypes.c_uint32)]


class _KeyInfo(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("attributes", ctypes.c_ubyte)]


class _Param(ctypes.Structure):
    _fields_ = [("key", ctypes.c_uint32), ("vers", _Vers),
                ("plimit", _PLimit), ("info", _KeyInfo),
                ("result", ctypes.c_ubyte), ("status", ctypes.c_ubyte),
                ("data8", ctypes.c_ubyte), ("data32", ctypes.c_uint32),
                ("bytes", ctypes.c_ubyte * 32)]


def decode(data_type: str, raw: bytes) -> Optional[float]:
    fmt = _FORMATS.get(data_type)
    if fmt is None or len(raw) != struct.calcsize(fmt):
        return None
    return struct.unpack(fmt, raw)[0]


def read_raw(keys=KEYS) -> Dict[str, Tuple[str, bytes]]:
    """{key: (type, raw bytes)} for readable whitelisted keys; {} on failure."""
    keys = [k for k in keys if k in KEYS]
    try:
        iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
        libc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("c"))
        iokit.IOServiceMatching.restype = ctypes.c_void_p
        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
        iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        iokit.IOServiceOpen.restype = ctypes.c_int
        iokit.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        iokit.IOConnectCallStructMethod.restype = ctypes.c_int
        iokit.IOConnectCallStructMethod.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
        iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]

        service = iokit.IOServiceGetMatchingService(
            0, iokit.IOServiceMatching(b"AppleSMC"))
        if not service:
            return {}
        conn = ctypes.c_uint32()
        task = ctypes.c_uint32.in_dll(libc, "mach_task_self_").value
        opened = iokit.IOServiceOpen(service, task, 0, ctypes.byref(conn))
        iokit.IOObjectRelease(service)
        if opened != 0:
            return {}

        def call(command, key, size=0):
            request = _Param(key=struct.unpack(">I", key.encode())[0],
                             data8=command)
            request.info.size = size
            reply = _Param()
            reply_size = ctypes.c_size_t(ctypes.sizeof(_Param))
            status = iokit.IOConnectCallStructMethod(
                conn.value, _SELECTOR, ctypes.byref(request),
                ctypes.sizeof(_Param), ctypes.byref(reply),
                ctypes.byref(reply_size))
            return reply if status == 0 and reply.result == 0 else None

        out = {}
        try:
            for key in keys:
                info = call(_CMD_KEY_INFO, key)
                if info is None or not 0 < info.info.size <= 32:
                    continue
                value = call(_CMD_READ, key, info.info.size)
                if value is None:
                    continue
                data_type = struct.pack(">I", info.info.type).decode("latin-1")
                out[key] = (data_type, bytes(value.bytes[:info.info.size]))
        finally:
            iokit.IOServiceClose(conn.value)
        return out
    except Exception:
        log.debug("SMC read failed", exc_info=True)
        return {}


def battery_extras(raw: Dict[str, Tuple[str, bytes]]) -> dict:
    """Decode whitelisted keys. Absent or implausible values stay None."""
    values = {key: decode(*raw[key]) for key in raw}
    cells = []
    for key in CELL_KEYS:
        mv = values.get(key)
        if mv is None:
            break
        if not 1000 <= mv <= 5000:
            cells = []
            break
        cells.append(int(mv))
    out = {name: None for name in LIFETIME_KEYS.values()}
    out["cell_voltage_mv"] = tuple(cells) if cells else None
    for key, name in LIFETIME_KEYS.items():
        value = values.get(key)
        if value is not None:
            out[name] = float(value) / 10.0 if key == "BLTA" else float(value)
    return out


def read_battery_extras() -> dict:
    return battery_extras(read_raw())
