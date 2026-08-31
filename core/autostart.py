import sys
import os
import winreg

APP_NAME = "ValorantRecorder"

def set_autostart(enable: bool):
    """
    Windowsのレジストリを操作して、PC起動時の自動起動を設定・解除する。
    """
    if sys.platform != "win32":
        return
        
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        )
        
        if enable:
            if getattr(sys, 'frozen', False):
                # PyInstaller等でビルドされたexeの場合
                app_path = f'"{sys.executable}"'
            else:
                # Pythonスクリプトとして実行されている場合
                app_path = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, app_path)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass # 既に存在しない場合は無視
                
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Failed to set autostart: {e}")
