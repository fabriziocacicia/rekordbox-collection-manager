"""Native macOS integration for application name, menu bar, and Dock branding."""

import os
import sys
from typing import Optional


def setup_macos_app_metadata(
    app_name: str = "Rekordbox Collection Manager",
    icon_path: Optional[str] = None,
) -> None:
    """Configure native macOS process name, bundle info, and dock icon via Cocoa runtime.

    Must be called before or immediately around QApplication initialization to
    ensure the macOS Dock tooltip and menu bar display the application name instead
    of the Python binary name (e.g. 'python3.12').
    """
    if sys.platform != "darwin":
        return

    try:
        import ctypes
        import ctypes.util

        # 1. Update BSD process name in libc
        try:
            libc = ctypes.cdll.LoadLibrary("libc.dylib")
            if hasattr(libc, "setprogname"):
                libc.setprogname(app_name.encode("utf-8"))
        except Exception:
            pass

        # 2. Configure Cocoa runtime objects
        objc_lib = ctypes.util.find_library("objc")
        if not objc_lib:
            return
        objc = ctypes.cdll.LoadLibrary(objc_lib)

        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p

        def msg(target, sel_name, *args, restype=ctypes.c_void_p, argtypes=None):
            if not target:
                return None
            sel = objc.sel_registerName(sel_name.encode("utf-8"))
            func = objc.objc_msgSend
            func.restype = restype
            if argtypes is None:
                func.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_void_p] * len(args)
            else:
                func.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + argtypes
            return func(target, sel, *args)

        NSString = objc.objc_getClass(b"NSString")
        new_title = msg(
            NSString,
            "stringWithUTF8String:",
            app_name.encode("utf-8"),
            argtypes=[ctypes.c_char_p],
        )

        # Update NSProcessInfo
        NSProcessInfo = objc.objc_getClass(b"NSProcessInfo")
        info = msg(NSProcessInfo, "processInfo")
        if info and new_title:
            msg(info, "setProcessName:", new_title)

        # Update NSBundle mainBundle infoDictionary (controls Dock tooltip and default menu title)
        NSBundle = objc.objc_getClass(b"NSBundle")
        mainBundle = msg(NSBundle, "mainBundle")
        if mainBundle:
            infoDict = msg(mainBundle, "infoDictionary")
            if infoDict and new_title:
                for key_bytes in (b"CFBundleName", b"CFBundleDisplayName"):
                    k = msg(NSString, "stringWithUTF8String:", key_bytes, argtypes=[ctypes.c_char_p])
                    msg(infoDict, "setObject:forKey:", new_title, k)

        # Update Dock Icon via NSApplication if an icon path is available
        if icon_path and os.path.exists(icon_path):
            path_str = msg(
                NSString,
                "stringWithUTF8String:",
                icon_path.encode("utf-8"),
                argtypes=[ctypes.c_char_p],
            )
            NSImage = objc.objc_getClass(b"NSImage")
            ns_img = msg(msg(NSImage, "alloc"), "initWithContentsOfFile:", path_str)
            if ns_img:
                NSApplication = objc.objc_getClass(b"NSApplication")
                ns_app = msg(NSApplication, "sharedApplication")
                if ns_app:
                    msg(ns_app, "setApplicationIconImage:", ns_img)

    except Exception:
        # Failsafe: never crash the app if macOS Cocoa introspection is unavailable
        pass


def update_macos_menu_title(app_name: str = "Rekordbox Collection Manager") -> None:
    """Update the first menu item in the macOS menu bar to match the application name."""
    if sys.platform != "darwin":
        return

    try:
        import ctypes
        import ctypes.util

        objc_lib = ctypes.util.find_library("objc")
        if not objc_lib:
            return
        objc = ctypes.cdll.LoadLibrary(objc_lib)

        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p

        def msg(target, sel_name, *args, restype=ctypes.c_void_p, argtypes=None):
            if not target:
                return None
            sel = objc.sel_registerName(sel_name.encode("utf-8"))
            func = objc.objc_msgSend
            func.restype = restype
            if argtypes is None:
                func.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_void_p] * len(args)
            else:
                func.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + argtypes
            return func(target, sel, *args)

        NSString = objc.objc_getClass(b"NSString")
        new_title = msg(
            NSString,
            "stringWithUTF8String:",
            app_name.encode("utf-8"),
            argtypes=[ctypes.c_char_p],
        )

        NSApplication = objc.objc_getClass(b"NSApplication")
        ns_app = msg(NSApplication, "sharedApplication")
        if ns_app:
            mainMenu = msg(ns_app, "mainMenu")
            if mainMenu:
                firstItem = msg(mainMenu, "itemAtIndex:", 0, argtypes=[ctypes.c_long])
                if firstItem and new_title:
                    msg(firstItem, "setTitle:", new_title)
    except Exception:
        pass
