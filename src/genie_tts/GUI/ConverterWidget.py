import sys
import os
import datetime

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit, QFileDialog,
    QListView, QTreeView, QAbstractItemView
)
from PySide6.QtCore import Signal, QObject, QSettings, QThread

# Lazy load converter to allow GUI inference without torch installed
def get_converter():
    from ..Converter.Converter import convert
    from ..Converter.v2.Converter import find_ckpt_and_pth
    return convert, find_ckpt_and_pth


def get_timestamp_msg(message: str, level: str = "INFO") -> str:
    """辅助函数：生成类似 Logging 格式的带时间戳字符串"""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return f"{now} - {level} - {message}"


class Worker(QObject):
    finished = Signal()
    log_signal = Signal(str)

    def __init__(self, folders):
        super().__init__()
        self.folders = folders

    def log(self, message: str, level: str = "INFO"):
        """内部辅助方法，用于格式化并发送日志"""
        formatted_msg = get_timestamp_msg(message, level)
        self.log_signal.emit(formatted_msg)

    def run(self):
        """执行转换任务"""
        try:
            try:
                convert, find_ckpt_and_pth = get_converter()
            except ImportError as e:
                self.log(f"模型转换功能需要安装 PyTorch：{e}", "ERROR")
                return

            root_output_dir = os.path.abspath("./Output")
            for folder in self.folders:
                character_name: str = os.path.basename(folder)
                output_dir: str = os.path.join(root_output_dir, character_name)
                if os.path.exists(output_dir):
                    self.log(f'输出文件夹 {output_dir} 已存在，将覆盖内容。', "WARNING")
                torch_ckpt_path, torch_pth_path = find_ckpt_and_pth(folder)
                if not torch_ckpt_path or not torch_pth_path:
                    self.log(f'无法处理文件夹 {folder} 。请保证文件夹内有 GPT—SOVITS V2 导出的 .pth 和 .ckpt 模型。',
                             "ERROR")
                    continue
                self.log(f'正在处理 {folder} 。')
                # 调用转换逻辑
                convert(torch_ckpt_path, torch_pth_path, output_dir)
                self.log(f'{folder} 处理完成。')  # 可选：提示完成
            os.startfile(root_output_dir)
        except Exception as e:
            self.log(f"任务执行过程中发生未捕获异常: {str(e)}", "ERROR")
        finally:
            self.finished.emit()


class ConverterWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('GENIE Converter (PySide6 Version)')
        self.resize(1280, 720)

        self.settings = QSettings("MyCompany", "GENIE Converter")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        self.folder_button = QPushButton('📂 选择一个或多个文件夹')
        self.folder_button.setFixedHeight(40)
        self.folder_button.clicked.connect(self.open_folder_dialog)

        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)

        main_layout.addWidget(self.folder_button)
        main_layout.addWidget(self.log_display)

        self.apply_stylesheet()

        self.thread = None
        self.worker = None

        self.append_formatted_log("欢迎使用 GENIE Converter!")
        self.append_formatted_log("支持将 GPT—SOVITS V2/V2ProPlus 模型导出为 GENIE 引擎所需的格式。")
        self.append_formatted_log("请选择一个或多个文件夹，每个文件夹中包含一对 .pth 和 .ckpt 文件。")
        self.append_formatted_log("您可以使用 Ctrl 或 Shift 键来进行多选。\n")

    def apply_stylesheet(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #2b2b2b;
                color: #f0f0f0;
                font-family: 'Segoe UI', 'Microsoft YaHei', 'Arial';
                font-size: 14px;
            }
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0056b3; }
            QPushButton:pressed { background-color: #004494; }
            QPushButton:disabled {
                background-color: #555;
                color: #aaa;
            }
            QTextEdit {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 5px;
                padding: 8px;
                font-family: 'Consolas', 'Courier New', monospace;
            }
            QScrollBar:vertical {
                border: none; background: #2b2b2b; width: 12px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #555; min-height: 20px; border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover { background: #007bff; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)

    def append_log(self, text: str):
        """直接给主线程调用的日志打印方法"""
        self.log_display.append(text)
        self.scroll_to_bottom()

    def append_formatted_log(self, text: str, level="INFO"):
        """给主线程调用的带格式日志方法"""
        msg = get_timestamp_msg(text, level)
        self.append_log(msg)

    def scroll_to_bottom(self):
        self.log_display.verticalScrollBar().setValue(
            self.log_display.verticalScrollBar().maximum()
        )

    def open_folder_dialog(self):
        last_dir = self.settings.value("last_dir", "")

        dialog = QFileDialog(self, '请选择文件夹', str(last_dir))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)

        list_view = dialog.findChild(QListView, 'listView')
        if list_view:
            list_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        tree_view = dialog.findChild(QTreeView)
        if tree_view:
            tree_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        if dialog.exec():
            selected_folders = dialog.selectedFiles()
            if selected_folders:
                self.run_conversion_task(selected_folders)
                self.settings.setValue("last_dir", os.path.dirname(selected_folders[0]))

    def run_conversion_task(self, folders):
        self.folder_button.setEnabled(False)
        self.folder_button.setText("🔄 转换中，请稍候...")

        self.thread = QThread()
        self.worker = Worker(folders)
        self.worker.moveToThread(self.thread)

        # 连接线程控制信号
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_conversion_finished)
        self.worker.log_signal.connect(self.append_log)

        self.thread.start()

    def on_conversion_finished(self):
        self.thread.quit()
        self.thread.wait()
        self.worker.deleteLater()
        self.thread.deleteLater()
        self.thread = None
        self.worker = None

        self.folder_button.setEnabled(True)
        self.folder_button.setText("📂 选择一个或多个文件夹")
        self.append_formatted_log("所有任务已完成。", "INFO")

    def closeEvent(self, event):
        if self.thread is not None and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait()
        super().closeEvent(event)


def start_gui() -> None:
    app = QApplication(sys.argv)
    window = ConverterWidget()
    window.show()
    sys.exit(app.exec())
