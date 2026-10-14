"""本機版伺服器（FastAPI）：只負責 HTTP，所有邏輯都在 routes.py（雲端版由 lambda_api.py 呼叫同一份）。"""
import mimetypes

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from .config import FRONTEND, GALLERY, MAX_UPLOAD
from . import checkin, routes
from .store import store

for ext, t in ((".webp", "image/webp"), (".js", "text/javascript"), (".css", "text/css")):
    mimetypes.add_type(t, ext)

app = FastAPI(title="HoYo Proxy Hub (local)", docs_url="/api-docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["content-type", "authorization", "x-bot-key"])


@app.put("/api/uploads/{job_id}")
async def receive_upload(job_id: str, request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"error": "圖片超過 10MB"}, status_code=413)
    status, err = await run_in_threadpool(routes.accept_upload, job_id, bytes(data))
    return JSONResponse({"error": err}, status_code=status) if err else Response(status_code=status)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def api(request: Request, rest: str):
    raw = await request.body()
    status, data = await run_in_threadpool(routes.handle, request.method, request.url.path,
                                           dict(request.headers), dict(request.query_params), raw)
    return JSONResponse(data, status_code=status)


@app.get("/get_gacha_url_{game_id}.ps1")
def gacha_url_script(game_id: str):
    body = routes.render_script(game_id)
    if body is None:
        return PlainTextResponse("unknown game", status_code=404)
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


app.mount("/gallery", StaticFiles(directory=GALLERY), name="gallery")
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")


@app.on_event("startup")
def _startup():
    store.purge_expired()
    checkin.start_scheduler()
    print(f"\n  HoYo Proxy Hub 本機版已啟動：http://{config.HOST}:{config.PORT}\n", flush=True)
