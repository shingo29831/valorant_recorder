"""Module: @role: 設定画面の共通UIコンポーネント（ホバー展開・ホイール切替コンボボックス）および保存先変更処理を提供するユーティリティ"""
import logging
import os
import shutil
import time
import traceback
from typing import Optional
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
    QWidget,
)
from PyQt6.QtCore import QEvent, QItemSelectionModel, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QCursor, QWheelEvent


class HoverWheelComboBox(QComboBox):
    """ホバー時にメニューを自動展開し、ホイール操作で選択項目を切り替えられるコンボボックス"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._popup_open: bool = False
        self._hover_latched: bool = False
        self._popup_opened_at: float = 0.0
        self._wheel_remainder: int = 0
        self._initial_cursor_pos: QPoint = QPoint()
        self._cursor_entered_rect: bool = False

        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(40)
        self._hover_timer.timeout.connect(self._check_hover_state)

        self._install_popup_filters()

    def _install_popup_filters(self) -> None:
        view = self.view()
        if view is not None:
            view.installEventFilter(self)
            viewport = view.viewport()
            if viewport is not None:
                viewport.installEventFilter(self)
            popup_win = view.window()
            if popup_win is not None and popup_win is not self and popup_win is not self.window():
                popup_win.installEventFilter(self)

    def _get_combo_global_rect(self, margin: int = 4) -> QRect:
        top_left = self.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, self.size()).adjusted(-margin, -margin, margin, margin)

    def _get_popup_global_rect(self, margin: int = 6) -> Optional[QRect]:
        view = self.view()
        if view is None:
            return None
        popup_win = view.window()
        if popup_win is None or not popup_win.isVisible():
            return None
        return popup_win.frameGeometry().adjusted(-margin, -margin, margin, margin)

    def _is_pos_in_combo_or_popup(self, global_pos: QPoint) -> bool:
        combo_rect = self._get_combo_global_rect(margin=4)
        if combo_rect.contains(global_pos):
            return True
        popup_rect = self._get_popup_global_rect(margin=6)
        if popup_rect is not None:
            if popup_rect.contains(global_pos):
                return True
            # 本体とポップアップ間の隙間移動でメニューが閉じないよう外接矩形で判定
            bridge_left = min(combo_rect.left(), popup_rect.left())
            bridge_right = max(combo_rect.right(), popup_rect.right())
            bridge_top = min(combo_rect.top(), popup_rect.top())
            bridge_bottom = max(combo_rect.bottom(), popup_rect.bottom())
            if QRect(
                bridge_left,
                bridge_top,
                bridge_right - bridge_left,
                bridge_bottom - bridge_top,
            ).contains(global_pos):
                return True
        return False

    def _sync_popup_selection(self, index: int) -> None:
        view = self.view()
        model = self.model()
        if view is None or model is None or index < 0 or index >= self.count():
            return
        model_idx = model.index(index, self.modelColumn(), self.rootModelIndex())
        if not model_idx.isValid():
            return
        view.setCurrentIndex(model_idx)
        sel_model = view.selectionModel()
        if sel_model is not None:
            sel_model.setCurrentIndex(
                model_idx,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        view.scrollTo(model_idx)
        viewport = view.viewport()
        if viewport is not None:
            viewport.update()

    def showPopup(self) -> None:
        if not self.isEnabled() or self.count() <= 0:
            return
        self._install_popup_filters()
        self._popup_open = True
        self._popup_opened_at = time.monotonic()
        super().showPopup()
        self._sync_popup_selection(self.currentIndex())
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def hidePopup(self) -> None:
        self._popup_open = False
        super().hidePopup()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if not self.isEnabled() or self.count() <= 0:
            return
        if QApplication.activeModalWidget() is not None:
            return
        if not self._hover_latched and not self._popup_open:
            self._hover_latched = True
            self._initial_cursor_pos = QCursor.pos()
            self._cursor_entered_rect = self._is_pos_in_combo_or_popup(self._initial_cursor_pos)
            self.showPopup()
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if not self._popup_open:
            if not self._get_combo_global_rect(margin=2).contains(QCursor.pos()):
                self._hover_latched = False

    def _check_hover_state(self) -> None:
        if not self.isVisible() or not self.isEnabled() or QApplication.activeModalWidget() is not None:
            if self._popup_open:
                self.hidePopup()
            self._hover_latched = False
            self._hover_timer.stop()
            return

        cursor_pos = QCursor.pos()
        if self._is_pos_in_combo_or_popup(cursor_pos):
            self._cursor_entered_rect = True
            return

        if not self._cursor_entered_rect and cursor_pos == self._initial_cursor_pos:
            return

        was_open = self._popup_open
        if self._popup_open:
            self.hidePopup()
        self._hover_latched = False
        self._hover_timer.stop()

        if was_open:
            QTimer.singleShot(0, self._trigger_hovered_sibling_combo)

    def _trigger_hovered_sibling_combo(self) -> None:
        cursor_pos = QCursor.pos()
        widget = QApplication.widgetAt(cursor_pos)
        while widget is not None:
            if isinstance(widget, HoverWheelComboBox) and widget is not self:
                if widget.isEnabled() and widget.isVisible():
                    widget._hover_latched = True
                    widget._initial_cursor_pos = cursor_pos
                    widget._cursor_entered_rect = True
                    widget.showPopup()
                break
            widget = widget.parentWidget()

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._handle_wheel_event(event)

    def _handle_wheel_event(self, event: QWheelEvent) -> bool:
        if not self.isEnabled() or self.count() <= 0:
            return False

        if not self._popup_open and self.isVisible() and QApplication.activeModalWidget() is None:
            self._hover_latched = True
            self._initial_cursor_pos = QCursor.pos()
            self._cursor_entered_rect = self._is_pos_in_combo_or_popup(self._initial_cursor_pos)
            self.showPopup()

        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x()
        if delta == 0:
            pixel_delta = event.pixelDelta().y()
            if pixel_delta != 0:
                delta = pixel_delta

        if delta == 0:
            event.accept()
            return True

        self._wheel_remainder += delta
        if abs(self._wheel_remainder) >= 120:
            steps = -int(self._wheel_remainder / 120)
            self._wheel_remainder %= 120 if self._wheel_remainder > 0 else -120
        else:
            steps = -1 if delta > 0 else 1
            self._wheel_remainder = 0

        new_index = max(0, min(self.count() - 1, self.currentIndex() + steps))
        if new_index != self.currentIndex():
            self.setCurrentIndex(new_index)
            self._sync_popup_selection(new_index)
        else:
            self._sync_popup_selection(self.currentIndex())

        event.accept()
        return True

    def eventFilter(self, obj, event) -> bool:
        ev_type = event.type()
        if ev_type == QEvent.Type.Wheel:
            if self._handle_wheel_event(event):
                return True
        elif ev_type == QEvent.Type.Hide:
            view = self.view()
            if view is not None and obj in (view, view.window()):
                self._popup_open = False
        elif ev_type == QEvent.Type.MouseButtonPress:
            # ホバー展開直後の反射的なコンボボックス本体クリックで閉じるのを防ぐ
            if self._popup_open and (time.monotonic() - self._popup_opened_at) < 0.35:
                global_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else QCursor.pos()
                if self._get_combo_global_rect(margin=0).contains(global_pos):
                    return True
        return super().eventFilter(obj, event)


def _should_append_suffix(selected_dir: str, suffix: str) -> bool:
    """選択されたフォルダに専用サブフォルダ名を付加すべきか判定する"""
    if not suffix:
        return False
    clean_suffix = suffix.strip("/\\").lower()
    base_name = os.path.basename(os.path.normpath(selected_dir)).lower()
    if base_name in (clean_suffix, "valoreco", "valorant_records", "valorant_clips", "clips"):
        return False
    if os.path.exists(selected_dir):
        try:
            entries = os.listdir(selected_dir)
            # ユーザーが新規作成した空フォルダや既に録画データが存在するフォルダはそのまま利用
            if not entries:
                return False
            if any(
                f.startswith("match_") and f.endswith((".mp4", ".json", ".jpg"))
                for f in entries
            ):
                return False
        except OSError:
            pass
    return True


def change_save_directory(parent_widget, current_dir, t, suffix="/valorant_records"):
    """
    保存先ディレクトリの変更と、既存ファイルの移行処理を行うユーティリティ関数。
    成功した場合は新しいディレクトリパスを返し、キャンセルされた場合はNoneを返す。
    """
    def write_log(msg: str) -> None:
        logging.info(f"[ChangeSaveDir] {msg}")

    write_log("--- Starting change_save_directory ---")
    try:
        initial_dir = current_dir if current_dir and os.path.exists(current_dir) else os.path.expanduser("~")
        selected_dir = QFileDialog.getExistingDirectory(
            parent_widget,
            t.select_directory,
            initial_dir,
        )
        write_log(f"QFileDialog closed. Selected: {selected_dir}")

        if not selected_dir:
            write_log("No directory selected. Aborting.")
            return None

        selected_norm = os.path.normpath(selected_dir)
        if _should_append_suffix(selected_norm, suffix):
            new_save_dir = os.path.normpath(os.path.join(selected_norm, suffix.strip("/\\")))
        else:
            new_save_dir = selected_norm

        old_save_dir = os.path.normpath(current_dir) if current_dir else ""
        write_log(f"Old dir: {old_save_dir}, New dir: {new_save_dir}")

        if old_save_dir and os.path.normcase(new_save_dir) == os.path.normcase(old_save_dir):
            write_log("New directory is the same as old directory. Aborting.")
            return None

        if os.path.exists(old_save_dir):
            files_to_copy = [f for f in os.listdir(old_save_dir) if os.path.isfile(os.path.join(old_save_dir, f))]
            
            if files_to_copy:
                reply_move = QMessageBox.question(
                    parent_widget,
                    t.move_files_title,
                    t.move_files_msg.format(old_dir=old_save_dir, new_dir=new_save_dir),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                
                if reply_move == QMessageBox.StandardButton.Yes:
                    write_log("User chose to move files. Starting copy process.")
                    os.makedirs(new_save_dir, exist_ok=True)
                    
                    total_bytes = sum(os.path.getsize(os.path.join(old_save_dir, f)) for f in files_to_copy)
                    copied_bytes = 0
                    
                    progress = QProgressDialog(t.copying_files, t.cancel, 0, 100, parent_widget)
                    progress.setWindowModality(Qt.WindowModality.WindowModal)
                    progress.setMinimumDuration(0)
                    progress.show()

                    cancel_copy = False
                    copied_files = []
                    for f in files_to_copy:
                        if cancel_copy:
                            break
                            
                        src = os.path.join(old_save_dir, f)
                        dst = os.path.join(new_save_dir, f)
                        
                        try:
                            progress.setLabelText(f"Copying: {f}")
                            QApplication.processEvents()
                            
                            length = 16 * 1024 * 1024 # 16MB chunks
                            with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                                while True:
                                    if progress.wasCanceled():
                                        write_log("Copy process canceled by user.")
                                        cancel_copy = True
                                        break
                                        
                                    buf = fsrc.read(length)
                                    if not buf:
                                        break
                                    fdst.write(buf)
                                    copied_bytes += len(buf)
                                    
                                    if total_bytes > 0:
                                        percent = int((copied_bytes / total_bytes) * 100)
                                        progress.setValue(percent)
                                    QApplication.processEvents()
                                    
                            if not cancel_copy:
                                shutil.copystat(src, dst)
                                copied_files.append(dst)
                                
                        except Exception as e:
                            write_log(f"Failed to copy {f}: {e}")
                    
                    if cancel_copy:
                        write_log("Cleaning up copied files due to cancellation.")
                        for dst_file in copied_files:
                            try:
                                if os.path.exists(dst_file):
                                    os.remove(dst_file)
                            except Exception as e:
                                write_log(f"Failed to remove {dst_file}: {e}")
                        
                        if 'dst' in locals() and os.path.exists(dst):
                            try:
                                os.remove(dst)
                            except Exception:
                                pass
                                
                        try:
                            if not os.listdir(new_save_dir):
                                os.rmdir(new_save_dir)
                        except Exception:
                            pass
                        
                        write_log("Cancellation cleanup finished. Aborting directory change.")
                        return None

                    progress.setValue(100)
                    write_log("Copy finished. Prompting for deletion.")
                    reply1 = QMessageBox.question(
                        parent_widget, 
                        t.delete_original_title, 
                        t.delete_original_msg.format(old_dir=old_save_dir),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    
                    if reply1 == QMessageBox.StandardButton.Yes:
                        reply2 = QMessageBox.question(
                            parent_widget,
                            t.confirm_deletion_title,
                            t.confirm_deletion_msg,
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        )
                        if reply2 == QMessageBox.StandardButton.Yes:
                            write_log("Deletion confirmed. Deleting files.")
                            for f in files_to_copy:
                                try:
                                    os.remove(os.path.join(old_save_dir, f))
                                except Exception as e:
                                    write_log(f"Failed to delete {f}: {e}")
                            try:
                                if not os.listdir(old_save_dir):
                                    os.rmdir(old_save_dir)
                                    write_log("Old directory removed.")
                            except Exception as e:
                                write_log(f"Failed to remove old directory: {e}")
                else:
                    write_log("User chose not to move files.")
                    os.makedirs(new_save_dir, exist_ok=True)
            else:
                write_log("No files to copy. Creating new directory.")
                os.makedirs(new_save_dir, exist_ok=True)
        else:
            write_log("Old directory does not exist. Creating new directory.")
            os.makedirs(new_save_dir, exist_ok=True)

        write_log("--- change_save_directory completed successfully ---")
        return new_save_dir

    except Exception as e:
        write_log(f"Exception in change_save_directory: {e}\n{traceback.format_exc()}")
        return None
