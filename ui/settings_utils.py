import os
import shutil
import datetime
import traceback
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QApplication
from PyQt6.QtCore import Qt

def change_save_directory(parent_widget, current_dir, t):
    """
    保存先ディレクトリの変更と、既存ファイルの移行処理を行うユーティリティ関数。
    成功した場合は新しいディレクトリパスを返し、キャンセルされた場合はNoneを返す。
    """
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dialog_debug.log")
    
    def write_log(msg):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {msg}\n")

    write_log("--- Starting change_save_directory ---")
    try:
        selected_dir = QFileDialog.getExistingDirectory(
            parent_widget, 
            t.select_directory,
            current_dir
        )
        write_log(f"QFileDialog closed. Selected: {selected_dir}")

        if not selected_dir:
            write_log("No directory selected. Aborting.")
            return None
        
        selected_dir = selected_dir.replace('\\', '/')
        if not selected_dir.endswith('/valorant_records'):
            new_save_dir = f"{selected_dir}/valorant_records"
        else:
            new_save_dir = selected_dir

        old_save_dir = current_dir.replace('\\', '/')
        write_log(f"Old dir: {old_save_dir}, New dir: {new_save_dir}")

        if new_save_dir == old_save_dir:
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
