"""Module: @role: リソースパス解決およびWindowsタイトルバー色設定などのUI基盤ユーティリティを提供するモジュール"""

import os
import sys
import ctypes


def get_resource_path(relative_path: str) -> str:
    """PyInstallerやNuitkaの実行時にもリソースパスを正しく解決する"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    # Nuitka対応: __file__ の場所からプロジェクトルートを計算
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def apply_custom_titlebar_color(win_id: int) -> None:
    """WindowsのDWM APIを使用してタイトルバーの色をアプリの背景色(#0F1923)に合わせる"""
    if sys.platform != "win32":
        return

    try:
        hwnd = int(win_id)
        dwmapi = ctypes.windll.dwmapi

        # Windows 10/11 ダークモードの適用 (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)
        dwmwa_use_immersive_dark_mode = 20
        value = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(hwnd, dwmwa_use_immersive_dark_mode, ctypes.byref(value), ctypes.sizeof(value))

        # Windows 11 タイトルバーの背景色変更 (DWMWA_CAPTION_COLOR = 35)
        dwmwa_caption_color = 35
        color = ctypes.c_int(0x0023190F)
        dwmapi.DwmSetWindowAttribute(hwnd, dwmwa_caption_color, ctypes.byref(color), ctypes.sizeof(color))
    except Exception as e:
        print(f"[WindowUtils] Failed to set titlebar color: {e}")
