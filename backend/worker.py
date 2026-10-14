"""背景工作 Lambda：
- S3 上傳事件 → Textract OCR（調頻紀錄 / 驅動盤）
- 非同步呼叫 action=import_url → 從官方網址匯入抽卡紀錄
- action=seed → 寫入預設配隊
"""
from urllib.parse import unquote_plus

from common import table, to_ddb, now_ts, new_id, ApiError, get_item
import gacha
import ocr
from gamedata import SEED_TEAMS


def set_job(job_id, status, message, **extra):
    names = {"#s": "status", "#m": "message"}
    values = {":s": status, ":m": message, ":u": now_ts()}
    expr = "SET #s = :s, #m = :m, updated = :u"
    for i, (k, v) in enumerate(extra.items()):
        names[f"#k{i}"] = k
        values[f":v{i}"] = to_ddb(v)
        expr += f", #k{i} = :v{i}"
    table.update_item(Key={"PK": f"JOB#{job_id}", "SK": "META"}, UpdateExpression=expr,
                      ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def handle_import_url(ev):
    job_id, pid = ev["job_id"], ev["pid"]
    try:
        set_job(job_id, "running", "開始匯入…")
        summary = gacha.import_from_url(pid, ev["url"], progress=lambda m: set_job(job_id, "running", m))
        stats = gacha.profile_stats(pid)
        gacha.update_global(pid, stats)
        n = sum(summary.values())
        set_job(job_id, "done", f"匯入完成，新增 {n} 筆紀錄", result=summary)
    except ApiError as e:
        set_job(job_id, "error", e.message)
    except Exception as e:  # noqa: BLE001
        print("import failed:", repr(e))
        set_job(job_id, "error", f"匯入失敗：{type(e).__name__}")


def handle_s3_record(rec):
    bucket = rec["s3"]["bucket"]["name"]
    key = unquote_plus(rec["s3"]["object"]["key"])
    parts = key.split("/")  # uploads/{kind}/{pid}/{job_id}
    if len(parts) != 4 or parts[0] != "uploads" or parts[1] not in ("gacha", "disc"):
        print("skip key", key)
        return
    _, kind, pid, job_id = parts
    job = get_item(f"JOB#{job_id}", "META")
    if not job or job["pid"] != pid:
        print("unknown job", key)
        return
    try:
        set_job(job_id, "running", "Textract 辨識中…")
        lines = ocr.detect_lines(bucket, key)
        raw = [ln["text"] for ln in lines]
        if kind == "gacha":
            rows = ocr.parse_gacha(lines)
            msg = f"辨識出 {len(rows)} 筆紀錄，請確認後匯入" if rows else "沒有辨識到紀錄，請確認遊戲語言為英文且截圖清晰"
            set_job(job_id, "done", msg, result={"rows": rows, "gacha_type": job.get("gacha_type")}, raw=raw)
        else:
            disc = ocr.parse_disc(lines)
            ok = disc["main"] or disc["subs"]
            set_job(job_id, "done", "辨識完成，請確認數值" if ok else "沒有辨識到詞條，請手動輸入",
                    result=disc, raw=raw)
    except Exception as e:  # noqa: BLE001
        print("ocr failed:", repr(e))
        set_job(job_id, "error", f"OCR 失敗：{type(e).__name__}")


def seed():
    if table.query(KeyConditionExpression="PK = :p", ExpressionAttributeValues={":p": "TEAMS"},
                   Limit=1).get("Items"):
        return {"seeded": 0}
    for i, (name, members, note) in enumerate(SEED_TEAMS):
        table.put_item(Item={
            "PK": "TEAMS", "SK": f"TEAM#{new_id(10)}", "name": name, "members": members,
            "note": note, "votes": len(SEED_TEAMS) - i, "author": "官方推薦", "created": now_ts(),
        })
    return {"seeded": len(SEED_TEAMS)}


def handler(event, context):
    if "Records" in event:
        for rec in event["Records"]:
            if rec.get("eventSource") == "aws:s3":
                handle_s3_record(rec)
        return {"ok": True}
    action = event.get("action")
    if action == "import_url":
        return handle_import_url(event)
    if action == "seed":
        return seed()
    return {"error": f"unknown action {action}"}
