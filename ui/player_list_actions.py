"""Module: @role: 録画一覧のフィルタ・ソートメニュー構築およびレコード削除・お気に入り切替・リネーム操作を担うモジュール"""
import json
import os
from typing import Callable, Optional
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QInputDialog, QMenu, QPushButton, QWidget
from ui.player_utils import find_video_for_json


def toggle_favorite_status(save_dir: str, json_filename: str) -> bool:
    json_path = os.path.join(save_dir, json_filename)
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        new_state = not data.get("is_favorite", False)
        data["is_favorite"] = new_state
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return new_state
    except Exception as e:
        print(f"[PlayerListActions] Error updating favorite status: {e}")
        return False


def reveal_record_in_explorer(save_dir: str, json_filename: str) -> None:
    import subprocess
    json_path = os.path.join(save_dir, json_filename)
    target_path = json_path
    try:
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            video_path = find_video_for_json(save_dir, json_filename, data)
            if video_path and os.path.exists(video_path):
                target_path = video_path
    except Exception:
        pass

    norm_target = os.path.normpath(target_path)
    if os.name == "nt" and os.path.exists(norm_target):
        subprocess.Popen(["explorer", "/select,", norm_target])
    elif os.path.exists(save_dir) and os.name == "nt":
        os.startfile(os.path.normpath(save_dir))


def delete_single_record_files(save_dir: str, json_filename: str) -> None:
    json_path = os.path.join(save_dir, json_filename)
    try:
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            video_path = find_video_for_json(save_dir, json_filename, data)
            thumb_path = json_path.replace(".json", ".jpg")

            os.remove(json_path)
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
    except Exception as e:
        print(f"[PlayerListActions] Error deleting record {json_filename}: {e}")


def prompt_and_rename_record(
    parent: QWidget, save_dir: str, json_filename: str, current_name: str
) -> bool:
    new_name, ok = QInputDialog.getText(parent, "Rename", "Enter new name:", text=current_name)
    if ok and new_name and new_name != current_name:
        json_path = os.path.join(save_dir, json_filename)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["custom_name"] = new_name
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"[PlayerListActions] Error saving custom name: {e}")
    return False


def show_record_filter_menu(
    parent: QWidget,
    anchor_btn: QPushButton,
    t: object,
    available_agents: set[str],
    available_maps: set[str],
    on_select: Callable[[Optional[tuple[str, object]]], None],
) -> None:
    menu = QMenu(parent)

    action_clear = menu.addAction(t.filter_clear)
    action_clear.triggered.connect(lambda checked=False: on_select(None))
    menu.addSeparator()

    action_fav = menu.addAction(t.filter_favorite)
    action_fav.triggered.connect(lambda checked=False: on_select(("favorite", True)))

    menu_agent = menu.addMenu(t.filter_agent)
    for agent in sorted(available_agents):
        action = menu_agent.addAction(agent)
        action.triggered.connect(lambda checked, a=agent: on_select(("agent", a)))

    menu_result = menu.addMenu(t.filter_result)
    for res, label in [("win", t.result_win), ("loss", t.result_loss), ("draw", t.result_draw)]:
        action = menu_result.addAction(label)
        action.triggered.connect(lambda checked, r=res: on_select(("result", r)))

    menu_map = menu.addMenu(t.filter_map)
    for m in sorted(available_maps):
        action = menu_map.addAction(m)
        action.triggered.connect(lambda checked, m_name=m: on_select(("map", m_name)))

    menu.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))


def show_record_sort_menu(
    parent: QWidget,
    anchor_btn: QPushButton,
    t: object,
    current_sort: str,
    on_select: Callable[[str], None],
) -> None:
    menu = QMenu(parent)
    sort_options = [
        ("date_desc", getattr(t, "sort_date_desc", "日付 (新しい順)")),
        ("date_asc", getattr(t, "sort_date_asc", "日付 (古い順)")),
        ("mmr_desc", getattr(t, "sort_mmr_desc", "MMR変動 (高い順)")),
        ("mmr_asc", getattr(t, "sort_mmr_asc", "MMR変動 (低い順)")),
    ]

    for sort_key, label in sort_options:
        action = menu.addAction(label)
        action.setCheckable(True)
        if current_sort == sort_key:
            action.setChecked(True)
        action.triggered.connect(lambda checked, s=sort_key: on_select(s))

    menu.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))
