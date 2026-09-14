import sys
import io

if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont
except ImportError:
    print("GUI mode requires PySide6. Install it with: pip install genie-tts[gui]")
    sys.exit(1)

from genie_tts.GUI.GUI import MainWindow

app = QApplication(sys.argv)
font = QFont("Microsoft YaHei", 10)
app.setFont(font)
window = MainWindow()
window.show()
sys.exit(app.exec())
