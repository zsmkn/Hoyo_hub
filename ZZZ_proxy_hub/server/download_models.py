"""預先下載 AI 模型（第一次安裝時執行，之後就不用在網頁上等）。"""
from . import config  # noqa: F401  設定 HF_HOME

if __name__ == "__main__":
    print("下載 Depth Anything V2（2.5D 立繪用，約 95MB）…", flush=True)
    from .depth import get_session
    get_session()
    print("下載 OCR 模型 …", flush=True)
    from .ocr import engine
    engine()
    print("完成！", flush=True)
