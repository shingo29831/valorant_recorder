"""Module: @role: 録画保存先ドライブの空き容量を監視しプログレスバーとして可視化するウィジェット"""

import os
import shutil
from typing import Optional
from PyQt6.QtWidgets import QProgressBar, QWidget


class StorageGaugeWidget(QProgressBar):
    """録画保存先ディレクトリの空き容量・使用率をグラフィカルに表示するプログレスバー"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setRange(0, 100)
        self.setFixedSize(210, 20)
        self.setTextVisible(True)

    def _resolve_existing_path(self, path: str) -> str:
        # 未作成の保存先パスでも親ディレクトリを辿ってディスク容量を取得
        target = os.path.abspath(path or ".")
        while not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent == target:
                break
            target = parent
        return target if os.path.exists(target) else "."

    def update_storage(self, save_dir: str, lang: str = "ja") -> None:
        """保存先ディレクトリの空き容量を取得してゲージとスタイルを更新する"""
        is_ja = (lang == 'ja')
        try:
            target_path = self._resolve_existing_path(save_dir)
            total, used, free = shutil.disk_usage(target_path)
            free_gb = free / (1024 ** 3)
            total_gb = total / (1024 ** 3)
            used_pct = int(round((used / total) * 100)) if total > 0 else 0
            used_pct = max(0, min(100, used_pct))

            if used_pct >= 90 or free_gb < 10.0:
                chunk_color = "#C62828"
            elif used_pct >= 80 or free_gb < 25.0:
                chunk_color = "#B45309"
            else:
                chunk_color = "#1565C0"

            label = f"空き容量: {free_gb:.1f} GB / {total_gb:.1f} GB" if is_ja else f"Free: {free_gb:.1f} GB / {total_gb:.1f} GB"
            self.setValue(used_pct)
            self.setFormat(label)
            self.setToolTip(f"{os.path.abspath(save_dir)}\n{label} ({100 - used_pct}%)")
        except Exception:
            chunk_color = "#555555"
            label = "空き容量: -- GB" if is_ja else "Free: -- GB"
            self.setValue(0)
            self.setFormat(label)

        self.setStyleSheet(f"""
            QProgressBar {{
                background-color: #1A242D;
                border: 1px solid #3A4550;
                border-radius: 4px;
                color: #FFFFFF;
                text-align: center;
                font-size: 11px;
                font-weight: bold;
            }}
            QProgressBar::chunk {{
                background-color: {chunk_color};
                border-radius: 3px;
            }}
        """)
