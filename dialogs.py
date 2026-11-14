"""Native Windows file dialogs via the procedural Win32 API.

Uses GetSaveFileNameW (comdlg32) and SHBrowseForFolderW (shell32) so dialogs
work from any thread without requiring the main UI thread.
"""

import ctypes
from ctypes import wintypes

comdlg32 = ctypes.WinDLL("comdlg32")
shell32 = ctypes.WinDLL("shell32")
ole32 = ctypes.WinDLL("ole32")

OFN_OVERWRITEPROMPT = 0x00000002
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_NOREADONLYRETURN = 0x00008000
BIF_RETURNONLYFSDIRS = 0x00000001
BIF_NEWDIALOGSTYLE = 0x00000040


class _OpenFileNameW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


class _BrowseInfoW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", wintypes.INT),
    ]


def _ensure_com():
    try:
        ole32.CoInitialize(None)
    except OSError:
        pass


def open_dialog(title: str, extension: str = "pem") -> str:
    """Show a native Open dialog for one existing file. Returns the chosen
    path or ''. Used to import a custom CA certificate (PEM)."""
    _ensure_com()
    buffer = ctypes.create_unicode_buffer(4096)
    buffer.value = ""
    ext = extension.lstrip(".")
    filter_text = f"{ext.upper()} files (*.{ext})\0*.{ext}\0All files (*.*)\0*.*\0"
    ofn = _OpenFileNameW()
    ofn.lStructSize = ctypes.sizeof(ofn)
    ofn.hwndOwner = None
    ofn.lpstrFilter = filter_text
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = 4096
    ofn.lpstrTitle = title
    ofn.Flags = OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST
    if comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return buffer.value
    return ""


def save_dialog(title: str, default_name: str = "", extension: str = "xlsx") -> str:
    """Show a native Save As dialog. Returns the chosen path or ''."""
    _ensure_com()
    buffer = ctypes.create_unicode_buffer(4096)
    buffer.value = default_name or ""
    ext = extension.lstrip(".")
    filter_text = f"{ext.upper()} files (*.{ext})\0*.{ext}\0All files (*.*)\0*.*\0"
    ofn = _OpenFileNameW()
    ofn.lStructSize = ctypes.sizeof(ofn)
    ofn.hwndOwner = None
    ofn.lpstrFilter = filter_text
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = 4096
    ofn.lpstrTitle = title
    ofn.Flags = OFN_PATHMUSTEXIST | OFN_OVERWRITEPROMPT | OFN_NOREADONLYRETURN
    ofn.lpstrDefExt = ext
    if comdlg32.GetSaveFileNameW(ctypes.byref(ofn)):
        return buffer.value
    return ""


def folder_dialog(title: str) -> str:
    """Show a native folder picker. Returns the chosen path or ''."""
    _ensure_com()
    display = ctypes.create_unicode_buffer(260)
    bi = _BrowseInfoW()
    bi.hwndOwner = None
    bi.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
    pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return ""
    path = ctypes.create_unicode_buffer(260)
    try:
        if shell32.SHGetPathFromIDListW(pidl, path):
            return path.value
        return ""
    finally:
        ole32.CoTaskMemFree(pidl)
