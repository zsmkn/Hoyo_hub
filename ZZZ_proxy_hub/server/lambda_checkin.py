"""雲端：EventBridge 每小時觸發一次，幫還沒簽到的人簽到（00:10 UTC+8 之後才會動作，失敗會自動重試）。"""
from datetime import datetime

from . import checkin


def handler(event, context):
    now = datetime.now(checkin.TZ8)
    if now.hour == 0 and now.minute < 10:
        return {"skipped": True}
    return {"results": checkin.run_due()}
