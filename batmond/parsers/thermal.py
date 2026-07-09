from __future__ import annotations

import ctypes
import ctypes.util
import logging

logger = logging.getLogger(__name__)

def read_raw_sensors() -> list[tuple[str, float]]:
    """
    Read raw temperature sensors via Apple's private IOHID API.
    Returns an empty list on any failure.
    """
    try:
        iokit_path = ctypes.util.find_library('IOKit')
        cf_path = ctypes.util.find_library('CoreFoundation')
        
        if not iokit_path or not cf_path:
            return []

        iokit = ctypes.cdll.LoadLibrary(iokit_path)
        cf = ctypes.cdll.LoadLibrary(cf_path)
        
        # Setup CF functions
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        
        cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), 
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, 
            ctypes.c_void_p, ctypes.c_void_p
        ]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        
        cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        cf.CFNumberCreate.restype = ctypes.c_void_p
        
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        
        cf.CFStringGetCStringPtr.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cf.CFStringGetCStringPtr.restype = ctypes.c_char_p
        
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        
        # Setup IOKit functions
        iokit.IOHIDEventSystemClientCreate.argtypes = [ctypes.c_void_p]
        iokit.IOHIDEventSystemClientCreate.restype = ctypes.c_void_p
        
        iokit.IOHIDEventSystemClientSetMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDEventSystemClientSetMatching.restype = ctypes.c_int
        
        iokit.IOHIDEventSystemClientCopyServices.argtypes = [ctypes.c_void_p]
        iokit.IOHIDEventSystemClientCopyServices.restype = ctypes.c_void_p
        
        iokit.IOHIDServiceClientCopyProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDServiceClientCopyProperty.restype = ctypes.c_void_p
        
        iokit.IOHIDServiceClientCopyEvent.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.c_long]
        iokit.IOHIDServiceClientCopyEvent.restype = ctypes.c_void_p
        
        iokit.IOHIDEventGetFloatValue.argtypes = [ctypes.c_void_p, ctypes.c_long]
        iokit.IOHIDEventGetFloatValue.restype = ctypes.c_double

        kCFAllocatorDefault = ctypes.c_void_p.in_dll(cf, "kCFAllocatorDefault")
        kCFTypeDictionaryKeyCallBacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        kCFTypeDictionaryValueCallBacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
        kCFNumberIntType = 9
        kCFStringEncodingUTF8 = 0x08000100
        
        def cfstr(s):
            return cf.CFStringCreateWithCString(kCFAllocatorDefault, s.encode('utf-8'), kCFStringEncodingUTF8)
            
        def cfnum(n):
            val = ctypes.c_int32(n)
            return cf.CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, ctypes.byref(val))
            
        def get_string(cf_str):
            if not cf_str: 
                return None
            ptr = cf.CFStringGetCStringPtr(cf_str, kCFStringEncodingUTF8)
            if ptr: 
                return ptr.decode('utf-8')
            buf = ctypes.create_string_buffer(256)
            if cf.CFStringGetCString(cf_str, buf, 256, kCFStringEncodingUTF8):
                return buf.value.decode('utf-8')
            return None

        client = iokit.IOHIDEventSystemClientCreate(kCFAllocatorDefault)
        if not client:
            return []

        # Match PrimaryUsagePage=0xff00, PrimaryUsage=0x0005
        key_page = cfstr("PrimaryUsagePage")
        key_usage = cfstr("PrimaryUsage")
        val_page = cfnum(0xff00)
        val_usage = cfnum(0x0005)
        
        keys = (ctypes.c_void_p * 2)(key_page, key_usage)
        vals = (ctypes.c_void_p * 2)(val_page, val_usage)

        match = cf.CFDictionaryCreate(
            kCFAllocatorDefault,
            keys,
            vals,
            2,
            ctypes.byref(kCFTypeDictionaryKeyCallBacks),
            ctypes.byref(kCFTypeDictionaryValueCallBacks)
        )

        iokit.IOHIDEventSystemClientSetMatching(client, match)
        services = iokit.IOHIDEventSystemClientCopyServices(client)

        cf.CFRelease(key_page)
        cf.CFRelease(key_usage)
        cf.CFRelease(val_page)
        cf.CFRelease(val_usage)
        cf.CFRelease(match)

        if not services:
            cf.CFRelease(client)
            return []

        count = cf.CFArrayGetCount(services)
        results = []
        
        key_product = cfstr("Product")
        
        kIOHIDEventTypeTemperature = 15
        field_base = kIOHIDEventTypeTemperature << 16

        for i in range(count):
            service = cf.CFArrayGetValueAtIndex(services, i)
            name_prop = iokit.IOHIDServiceClientCopyProperty(service, key_product)
            name = get_string(name_prop)
            if name_prop: 
                cf.CFRelease(name_prop)
                
            if not name:
                continue

            event = iokit.IOHIDServiceClientCopyEvent(service, kIOHIDEventTypeTemperature, 0, 0)
            if event:
                temp = iokit.IOHIDEventGetFloatValue(event, field_base)
                results.append((name, float(temp)))
                cf.CFRelease(event)

        cf.CFRelease(key_product)
        cf.CFRelease(services)
        cf.CFRelease(client)

        return results

    except Exception as e:
        logger.debug(f"Failed to read IOHID sensors: {e}")
        return []

def aggregate_temps(sensors: list[tuple[str, float]]) -> dict[str, float | None]:
    """
    Extracts soc_temp_c (max of 'PMU tdie*') and ssd_temp_c (max of '*NAND*').
    Filters out extreme/invalid values (<=0 or >=130).
    """
    soc_temps = []
    ssd_temps = []
    
    for name, temp in sensors:
        if not (0 < temp < 130):
            continue
            
        if name.startswith("PMU tdie"):
            soc_temps.append(temp)
        elif "NAND" in name:
            ssd_temps.append(temp)
            
    return {
        "soc_temp_c": max(soc_temps) if soc_temps else None,
        "ssd_temp_c": max(ssd_temps) if ssd_temps else None
    }
