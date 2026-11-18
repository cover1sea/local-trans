"""Local Trans: PLaMo 3 Translate 0.8B によるスクリーンショット英日翻訳。"""
import logging
import os
import sys

from localtrans.config import APP_DIR, Config


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(APP_DIR / "local-trans.log", encoding="utf-8"),
                  *( [logging.StreamHandler()] if sys.stderr else [])],
    )
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

    # タスクバーで python.exe と別のアプリとして扱わせる (これがないと Python のアイコンになる)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalTrans.App")
    except Exception:  # noqa: BLE001
        pass

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from localtrans.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Local Trans")
    icon = APP_DIR / "assets" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    # 最小表示では本体を隠すので、ダイアログを閉じただけで終了しないようにする
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow(Config.load())
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
