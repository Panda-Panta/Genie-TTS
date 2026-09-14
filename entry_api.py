import sys
import os
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genie-TTS API Server")
    parser.add_argument("--cli", action="store_true", help="Run in CLI headless mode without GUI")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--workers", type=int, default=1, help="Number of workers (default: 1)")
    args = parser.parse_args()

    if args.cli:
        from genie_tts.Server import app
        import uvicorn
        print(f"Starting Genie-TTS API Server (Headless) at http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port, workers=args.workers)
    else:
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QFont
            from genie_tts.GUI.ApiServerWidget import ApiServerWindow

            app = QApplication(sys.argv)
            font = QFont("Microsoft YaHei", 9)
            app.setFont(font)
            win = ApiServerWindow()
            win.show()
            sys.exit(app.exec())
        except Exception as e:
            print(f"Failed to start API GUI: {e}. Falling back to CLI mode...")
            from genie_tts.Server import app
            import uvicorn
            uvicorn.run(app, host=args.host, port=args.port, workers=args.workers)
