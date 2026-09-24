"""Solo macOS: premere un pulsante dell'app Claude con le API di accessibilità (ctypes, niente pyobjc).

Serve il permesso "Accessibilità" per il programma che esegue il demone (Impostazioni di Sistema →
Privacy e sicurezza → Accessibilità). Con JXA/osascript la stessa ricerca richiede ~3 s, perché ogni
proprietà letta è un Apple Event; con le API native bastano pochi millisecondi.

Come su Windows, Chromium costruisce l'albero di accessibilità solo quando qualcuno lo chiede:
si accende AXManualAccessibility sull'app e si riprova qualche volta.
"""

from __future__ import annotations

import ctypes
import subprocess
import time
from collections.abc import Callable
from ctypes import byref, c_bool, c_char_p, c_int32, c_long, c_uint32, c_void_p

_AS = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
_CF = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

_UTF8 = 0x08000100  # kCFStringEncodingUTF8

_CF.CFStringCreateWithCString.restype = c_void_p
_CF.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
_CF.CFStringGetLength.restype = c_long
_CF.CFStringGetLength.argtypes = [c_void_p]
_CF.CFStringGetCString.restype = c_bool
_CF.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
_CF.CFGetTypeID.restype = c_long
_CF.CFGetTypeID.argtypes = [c_void_p]
_CF.CFStringGetTypeID.restype = c_long
_CF.CFArrayGetTypeID.restype = c_long
_CF.CFArrayGetCount.restype = c_long
_CF.CFArrayGetCount.argtypes = [c_void_p]
_CF.CFArrayGetValueAtIndex.restype = c_void_p
_CF.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
_CF.CFRelease.argtypes = [c_void_p]

_AS.AXIsProcessTrusted.restype = c_bool
_AS.AXUIElementCreateApplication.restype = c_void_p
_AS.AXUIElementCreateApplication.argtypes = [c_int32]
_AS.AXUIElementCopyAttributeValue.restype = c_int32
_AS.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, ctypes.POINTER(c_void_p)]
_AS.AXUIElementSetAttributeValue.restype = c_int32
_AS.AXUIElementSetAttributeValue.argtypes = [c_void_p, c_void_p, c_void_p]
_AS.AXUIElementPerformAction.restype = c_int32
_AS.AXUIElementPerformAction.argtypes = [c_void_p, c_void_p]

_TRUE = c_void_p.in_dll(_CF, "kCFBooleanTrue")
_STRING_TYPE = _CF.CFStringGetTypeID()
_ARRAY_TYPE = _CF.CFArrayGetTypeID()
_names: dict[str, int] = {}


def _cfstr(text: str) -> int:
    """CFString costante (tenuta per tutta la vita del processo: sono pochi nomi di attributi)."""
    if text not in _names:
        _names[text] = _CF.CFStringCreateWithCString(None, text.encode(), _UTF8)
    return _names[text]


def _to_str(ref: int) -> str:
    size = _CF.CFStringGetLength(ref) * 4 + 1
    buf = ctypes.create_string_buffer(size)
    return buf.value.decode() if _CF.CFStringGetCString(ref, buf, size, _UTF8) else ""


def _copy(element: int, attribute: str) -> int | None:
    """Valore di un attributo (da rilasciare con CFRelease), o None."""
    out = c_void_p()
    if _AS.AXUIElementCopyAttributeValue(element, _cfstr(attribute), byref(out)) != 0 or not out.value:
        return None
    return out.value


def _string(element: int, attribute: str) -> str:
    ref = _copy(element, attribute)
    if ref is None:
        return ""
    try:
        return _to_str(ref) if _CF.CFGetTypeID(ref) == _STRING_TYPE else ""
    finally:
        _CF.CFRelease(ref)


def trusted() -> bool:
    """True se questo processo ha il permesso Accessibilità."""
    return bool(_AS.AXIsProcessTrusted())


def app_pid(name: str = "Claude") -> int | None:
    try:
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out[0]) if out else None


def press_button(pid: int, match: Callable[[str], bool], tries: int = 8, max_nodes: int = 5000) -> str | None:
    """Cerca nella finestra dell'app un AXButton il cui nome soddisfa `match` e lo preme.

    Ritorna il nome del pulsante premuto, None se non c'è. Ricerca in ampiezza: la barra laterale sta in
    alto nell'albero, il contenuto della chat (molto più grande) più in basso.
    """
    app = _AS.AXUIElementCreateApplication(pid)
    try:
        _AS.AXUIElementSetAttributeValue(app, _cfstr("AXManualAccessibility"), _TRUE)
        for attempt in range(tries):
            name = _search(app, match, max_nodes)
            if name is not None:
                return name
            time.sleep(0.4)  # albero non ancora costruito (prima richiesta) o finestra in caricamento
        return None
    finally:
        _CF.CFRelease(app)


def _search(app: int, match: Callable[[str], bool], max_nodes: int) -> str | None:
    arrays = []  # gli elementi dentro un CFArray vivono finché vive l'array: si rilasciano alla fine
    try:
        windows = _copy(app, "AXWindows")
        if windows is None:
            return None
        arrays.append(windows)
        level = [_CF.CFArrayGetValueAtIndex(windows, i) for i in range(_CF.CFArrayGetCount(windows))]
        visited = 0
        while level and visited < max_nodes:
            nxt = []
            for element in level:
                visited += 1
                if _string(element, "AXRole") == "AXButton":
                    name = _string(element, "AXTitle") or _string(element, "AXDescription")
                    if name and match(name):
                        _AS.AXUIElementPerformAction(element, _cfstr("AXPress"))
                        return name
                kids = _copy(element, "AXChildren")
                if kids is None:
                    continue
                if _CF.CFGetTypeID(kids) != _ARRAY_TYPE:
                    _CF.CFRelease(kids)
                    continue
                arrays.append(kids)
                nxt.extend(_CF.CFArrayGetValueAtIndex(kids, i) for i in range(_CF.CFArrayGetCount(kids)))
            level = nxt
        return None
    finally:
        for ref in arrays:
            _CF.CFRelease(ref)
