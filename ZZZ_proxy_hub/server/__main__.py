"""啟動：python -m server"""
import uvicorn

from .config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run("server.app:app", host=HOST, port=PORT, log_level="warning")
