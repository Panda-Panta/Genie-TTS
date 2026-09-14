import sys
import os
import json
import logging
import threading
import webbrowser
from typing import Optional, Dict

import uvicorn
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QComboBox, QGroupBox, QFormLayout, QMessageBox, QTextEdit
)
from PySide6.QtCore import Qt, Signal, Slot, QObject, QThread
from PySide6.QtGui import QTextCursor, QFont, QCloseEvent

from ..Server import app, set_server_reference_audio
from ..ModelManager import model_manager
from ..Utils.Language import normalize_language
from ..Internal import load_character, set_reference_audio
from ..Core.Inference import tts_client
from ..Utils.Shared import context


def resolve_path(p: str) -> str:
    if not p:
        return ""
    if os.path.isabs(p) and os.path.exists(p):
        return p
    if os.path.exists(p):
        return os.path.abspath(p)
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        cand = os.path.join(exe_dir, p)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return os.path.abspath(p)


def get_config_file_path() -> str:
    cand1 = os.path.abspath("./UserData/GenieGuiConfig.json")
    if os.path.exists(cand1):
        return cand1
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        cand2 = os.path.join(exe_dir, "UserData", "GenieGuiConfig.json")
        if os.path.exists(cand2):
            return cand2
        return cand2
    return cand1


class LogRedirector(QObject):
    textWritten = Signal(str)

    def __init__(self):
        super().__init__()
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr

    def write(self, text: str):
        if text:
            self.textWritten.emit(str(text))
            if self._old_stdout is not None:
                try:
                    self._old_stdout.write(text)
                except Exception:
                    pass

    def flush(self):
        pass


class QtLogHandler(logging.Handler):
    def __init__(self, redirector: LogRedirector):
        super().__init__()
        self.redirector = redirector

    def emit(self, record):
        try:
            msg = self.format(record)
            self.redirector.write(msg + "\n")
        except Exception:
            pass


class UvicornServerThread(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.server: Optional[uvicorn.Server] = None

    def run(self):
        config = uvicorn.Config(app=app, host=self.host, port=self.port, log_level="info")
        self.server = uvicorn.Server(config)
        self.server.run()

    def stop(self):
        if self.server:
            self.server.should_exit = True


class StartupWarmupWorker(QThread):
    finished_signal = Signal(bool, str)
    log_signal = Signal(str)

    def __init__(self, char_name: str, model_dir: str, ref_audio: str, ref_text: str, lang: str):
        super().__init__()
        self.char_name = char_name
        self.model_dir = model_dir
        self.ref_audio = ref_audio
        self.ref_text = ref_text
        self.lang = lang

    def run(self):
        try:
            norm_lang = normalize_language(self.lang)

            self.log_signal.emit(f"[INFO] 正在载入角色模型 '{self.char_name}'...")
            load_character(character_name=self.char_name, onnx_model_dir=self.model_dir, language=norm_lang)
            self.log_signal.emit(f"[INFO] 角色模型 '{self.char_name}' 加载完成。")

            self.log_signal.emit(f"[INFO] 正在提取参考音频特征并绑定: {self.ref_audio}")
            set_reference_audio(character_name=self.char_name, audio_path=self.ref_audio, audio_text=self.ref_text, language=norm_lang)
            set_server_reference_audio(character_name=self.char_name, audio_path=self.ref_audio, audio_text=self.ref_text, language=norm_lang)
            self.log_signal.emit(f"[INFO] 参考音频特征提取就绪。")

            self.log_signal.emit("[INFO] 正在执行模型初次推理与预热 (Warm-up)...")
            gsv_model = model_manager.get(self.char_name)
            warmup_char = "你好" if norm_lang in ["zh", "chinese"] else ("Hello" if norm_lang in ["en", "english"] else "あ")
            tts_client.stop_event.clear()
            _ = tts_client.tts(
                text=warmup_char,
                prompt_audio=context.current_prompt_audio,
                encoder=gsv_model.T2S_ENCODER,
                first_stage_decoder=gsv_model.T2S_FIRST_STAGE_DECODER,
                stage_decoder=gsv_model.T2S_STAGE_DECODER,
                vocoder=gsv_model.VITS,
                prompt_encoder=gsv_model.PROMPT_ENCODER,
                language=gsv_model.LANGUAGE,
            )
            self.log_signal.emit(f"[INFO] ✅ 角色【{self.char_name}】加载与预热全部完成！模型已常驻内存。")
            self.finished_signal.emit(True, f"角色【{self.char_name}】预热完成，API已就绪！")
        except Exception as e:
            self.log_signal.emit(f"[ERROR] 预热过程出错: {e}")
            self.finished_signal.emit(False, str(e))


class ApiServerWidget(QWidget):
    """API 服务端管理面板，包含预设选择、一键启动预热、停止及实时日志"""

    def __init__(self, tts_widget=None, parent: QWidget = None):
        super().__init__(parent)
        self.tts_widget = tts_widget
        self.server_thread: Optional[UvicornServerThread] = None
        self.warmup_worker: Optional[StartupWarmupWorker] = None
        self.presets: Dict[str, dict] = {}
        self.redirector = LogRedirector()
        self.redirector.textWritten.connect(self._append_log)

        sys.stdout = self.redirector
        sys.stderr = self.redirector

        # 连接 logging 输出
        root_logger = logging.getLogger()
        self.log_handler = QtLogHandler(self.redirector)
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        root_logger.addHandler(self.log_handler)

        self._init_ui()
        self.load_presets_from_disk()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # 1. 顶部预设选择与服务参数
        group_config = QGroupBox("📋 预设选择与服务配置")
        layout_config = QVBoxLayout()
        form_config = QFormLayout()

        # 预设选择行
        hbox_preset = QHBoxLayout()
        self.combo_presets = QComboBox()
        self.combo_presets.setFixedHeight(30)
        self.combo_presets.currentTextChanged.connect(self._on_preset_changed)

        btn_refresh = QPushButton("🔄 刷新预设")
        btn_refresh.setFixedWidth(100)
        btn_refresh.setFixedHeight(30)
        btn_refresh.clicked.connect(self.load_presets_from_disk)

        hbox_preset.addWidget(self.combo_presets, 1)
        hbox_preset.addWidget(btn_refresh)
        form_config.addRow("选择说话人预设:", hbox_preset)

        # 详情展示
        self.txt_model_dir = QLineEdit()
        self.txt_model_dir.setReadOnly(True)
        self.txt_ref_audio = QLineEdit()
        self.txt_ref_audio.setReadOnly(True)
        self.txt_ref_text = QLineEdit()
        self.txt_ref_text.setReadOnly(True)
        self.txt_lang = QLineEdit()
        self.txt_lang.setReadOnly(True)

        form_config.addRow("模型目录 (ONNX):", self.txt_model_dir)
        form_config.addRow("参考音频文件:", self.txt_ref_audio)
        form_config.addRow("参考音频文本:", self.txt_ref_text)
        form_config.addRow("目标语言:", self.txt_lang)

        # 服务 IP 与端口
        hbox_network = QHBoxLayout()
        self.input_host = QLineEdit("0.0.0.0")
        self.input_host.setFixedHeight(28)
        self.input_port = QLineEdit("8000")
        self.input_port.setFixedHeight(28)
        hbox_network.addWidget(QLabel("监听地址:"))
        hbox_network.addWidget(self.input_host, 2)
        hbox_network.addWidget(QLabel("端口号:"))
        hbox_network.addWidget(self.input_port, 1)

        form_config.addRow("网络配置:", hbox_network)
        layout_config.addLayout(form_config)
        group_config.setLayout(layout_config)
        main_layout.addWidget(group_config)

        # 2. 控制操作栏
        group_control = QGroupBox("⚡ 运行状态与控制")
        layout_control = QVBoxLayout()

        self.lbl_status = QLabel("⚪ 服务未启动")
        self.lbl_status.setStyleSheet("font-size: 11pt; font-weight: bold; color: gray;")

        hbox_actions = QHBoxLayout()
        self.btn_start = QPushButton("▶️ 启动 API 服务 (自动加载与预热)")
        self.btn_start.setFixedHeight(40)
        self.btn_start.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                font-size: 11pt;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #cccccc; color: #666666; }
        """)
        self.btn_start.clicked.connect(self.start_server_and_warmup)

        self.btn_stop = QPushButton("⏹️ 停止 API 服务")
        self.btn_stop.setFixedHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                font-size: 11pt;
            }
            QPushButton:hover { background-color: #d32f2f; }
            QPushButton:disabled { background-color: #cccccc; color: #666666; }
        """)
        self.btn_stop.clicked.connect(self.stop_server)

        self.btn_docs = QPushButton("🌐 打开 API 文档")
        self.btn_docs.setFixedHeight(40)
        self.btn_docs.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
                border-radius: 5px;
                font-size: 11pt;
            }
            QPushButton:hover { background-color: #1976D2; }
        """)
        self.btn_docs.clicked.connect(self.open_docs_in_browser)

        hbox_actions.addWidget(self.btn_start, 2)
        hbox_actions.addWidget(self.btn_stop, 1)
        hbox_actions.addWidget(self.btn_docs, 1)

        layout_control.addWidget(self.lbl_status)
        layout_control.addLayout(hbox_actions)
        group_control.setLayout(layout_control)
        main_layout.addWidget(group_control)

        # 3. 实时日志展示区
        group_logs = QGroupBox("📜 服务与推理实时日志")
        layout_logs = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            "background-color: #1e1e1e; color: #ecf0f1; font-family: Consolas, monospace; font-size: 10pt;"
        )
        hbox_log_btn = QHBoxLayout()
        btn_clear_log = QPushButton("🗑️ 清空日志")
        btn_clear_log.setFixedWidth(90)
        btn_clear_log.clicked.connect(self.log_text.clear)
        hbox_log_btn.addStretch()
        hbox_log_btn.addWidget(btn_clear_log)

        layout_logs.addWidget(self.log_text)
        layout_logs.addLayout(hbox_log_btn)
        group_logs.setLayout(layout_logs)
        main_layout.addWidget(group_logs, 1)

    def load_presets_from_disk(self):
        config_path = get_config_file_path()
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.presets = json.load(f)
                self._append_log(f"[INFO] 成功读取配置预设文件: {config_path}")
            except Exception as e:
                self._append_log(f"[ERROR] 读取预设文件失败: {e}")
                self.presets = {}
        else:
            self._append_log(f"[WARN] 未找到预设文件 {config_path}，将采用默认预设。")
            self.presets = {}

        if not self.presets:
            self.presets = {
                "Default (Mika)": {
                    "genie_dir": "CharacterModels/v2ProPlus/mika/tts_models",
                    "ref_audio": "CharacterModels/v2ProPlus/mika/prompt_wav/917575.wav",
                    "ref_text": "私も昔、これと似たようなの持ってたなぁ…。",
                    "lang": "Japanese"
                }
            }

        prev_text = self.combo_presets.currentText()
        self.combo_presets.blockSignals(True)
        self.combo_presets.clear()
        self.combo_presets.addItems(list(self.presets.keys()))
        if prev_text in self.presets:
            self.combo_presets.setCurrentText(prev_text)
        elif self.combo_presets.count() > 0:
            self.combo_presets.setCurrentIndex(0)
        self.combo_presets.blockSignals(False)

        self._on_preset_changed(self.combo_presets.currentText())

    def _on_preset_changed(self, preset_name: str):
        data = self.presets.get(preset_name, {})
        self.txt_model_dir.setText(data.get("genie_dir", ""))
        self.txt_ref_audio.setText(data.get("ref_audio", ""))
        self.txt_ref_text.setText(data.get("ref_text", ""))
        self.txt_lang.setText(data.get("lang", "Japanese"))

    @Slot(str)
    def _append_log(self, text: str):
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)
        self.log_text.insertPlainText(text)
        if not text.endswith("\n"):
            self.log_text.insertPlainText("\n")
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)

    def start_server_and_warmup(self):
        char_name = self.combo_presets.currentText().strip() or "Default"
        raw_model_dir = self.txt_model_dir.text().strip()
        raw_ref_audio = self.txt_ref_audio.text().strip()
        ref_text = self.txt_ref_text.text().strip()
        lang = self.txt_lang.text().strip() or "Chinese"

        model_dir = resolve_path(raw_model_dir)
        ref_audio = resolve_path(raw_ref_audio)

        if not os.path.exists(model_dir):
            QMessageBox.critical(self, "错误", f"找不到模型目录:\n{model_dir}\n请在 GUI 中检查该预设的模型路径！")
            return
        if not os.path.exists(ref_audio):
            QMessageBox.critical(self, "错误", f"找不到参考音频文件:\n{ref_audio}\n请在 GUI 中检查该预设的音频路径！")
            return
        if not ref_text:
            QMessageBox.warning(self, "提示", "参考音频对应文本不能为空！")
            return

        host = self.input_host.text().strip() or "0.0.0.0"
        try:
            port = int(self.input_port.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "端口号必须为有效整数！")
            return

        # 启动 Uvicorn 服务
        if not self.server_thread or not self.server_thread.is_alive():
            try:
                self.server_thread = UvicornServerThread(host=host, port=port)
                self.server_thread.start()
                self._append_log(f"[INFO] API 核心服务启动: http://{host}:{port}")
            except Exception as e:
                QMessageBox.critical(self, "启动失败", f"无法启动 Uvicorn 监听: {e}")
                return

        # 更新状态与控件
        self.lbl_status.setText("🟡 正在加载模型并执行热身 (Warm-up)...")
        self.lbl_status.setStyleSheet("font-size: 11pt; font-weight: bold; color: #FF9800;")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.combo_presets.setEnabled(False)
        self.input_host.setEnabled(False)
        self.input_port.setEnabled(False)

        # 启动异步工作线程执行预加载与试音
        self.warmup_worker = StartupWarmupWorker(
            char_name=char_name,
            model_dir=model_dir,
            ref_audio=ref_audio,
            ref_text=ref_text,
            lang=lang
        )
        self.warmup_worker.log_signal.connect(self._append_log)
        self.warmup_worker.finished_signal.connect(self._on_warmup_finished)
        self.warmup_worker.start()

    def _on_warmup_finished(self, success: bool, msg: str):
        host = self.input_host.text().strip() or "0.0.0.0"
        port = self.input_port.text().strip() or "8000"
        if success:
            self.lbl_status.setText(f"🟢 服务运行中 (http://{host}:{port}) - 角色【{self.combo_presets.currentText()}】就绪")
            self.lbl_status.setStyleSheet("font-size: 11pt; font-weight: bold; color: #4CAF50;")
        else:
            self.lbl_status.setText("🔴 模型预热失败，请查看下方日志")
            self.lbl_status.setStyleSheet("font-size: 11pt; font-weight: bold; color: #f44336;")
            QMessageBox.warning(self, "预热警告", f"模型初始化过程出现异常:\n{msg}\n服务仍在监听，但此角色可能尚未能正常推理。")

    def stop_server(self):
        if self.warmup_worker and self.warmup_worker.isRunning():
            self.warmup_worker.terminate()
            self.warmup_worker.wait()

        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.stop()
            self.server_thread = None
            self._append_log("[INFO] API 服务已停止。")

        self.lbl_status.setText("⚪ 服务已停止")
        self.lbl_status.setStyleSheet("font-size: 11pt; font-weight: bold; color: gray;")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.combo_presets.setEnabled(True)
        self.input_host.setEnabled(True)
        self.input_port.setEnabled(True)

    def open_docs_in_browser(self):
        port = self.input_port.text().strip() or "8000"
        webbrowser.open(f"http://127.0.0.1:{port}/docs")

    def close(self):
        self.stop_server()


class ApiServerWindow(QMainWindow):
    """独立的 API 服务桌面端窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🔮 Genie-TTS API 服务控制台")
        self.resize(920, 720)
        self.api_widget = ApiServerWidget(parent=self)
        self.setCentralWidget(self.api_widget)

    def closeEvent(self, event: QCloseEvent):
        self.api_widget.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    win = ApiServerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
