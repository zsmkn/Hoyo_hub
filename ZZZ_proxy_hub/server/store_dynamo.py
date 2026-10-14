"""DynamoDB 版的 PK/SK 儲存（雲端用），介面與 store.Store（SQLite）完全相同。

資料表結構：PK（字串）、SK（字串）、d（JSON 字串，存實際資料）、ttl（數字，DynamoDB TTL）
"""
import json
import time

import boto3
from boto3.dynamodb.conditions import Key


class DynamoStore:
    def __init__(self, table_name):
        self.table = boto3.resource("dynamodb").Table(table_name)

    @staticmethod
    def _row(item):
        # DynamoDB 的 TTL 刪除可能延遲到 48 小時，讀取時自己再過濾一次
        if item.get("ttl") is not None and int(item["ttl"]) <= time.time():
            return None
        d = json.loads(item["d"])
        d["PK"], d["SK"] = item["PK"], item["SK"]
        return d

    @staticmethod
    def _item(pk, sk, data, ttl=None):
        data = {k: v for k, v in data.items() if k not in ("PK", "SK")}
        item = {"PK": pk, "SK": sk, "d": json.dumps(data, ensure_ascii=False)}
        if ttl:
            item["ttl"] = int(ttl)
        return item

    def get(self, pk, sk):
        r = self.table.get_item(Key={"PK": pk, "SK": sk}).get("Item")
        return self._row(r) if r else None

    def _raw(self, pk, sk):
        return self.table.get_item(Key={"PK": pk, "SK": sk}).get("Item")

    def put(self, pk, sk, data, ttl=None):
        self.table.put_item(Item=self._item(pk, sk, data, ttl))

    def put_many(self, rows):
        with self.table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as bw:
            for pk, sk, d in rows:
                bw.put_item(Item=self._item(pk, sk, d))

    def put_if_absent(self, pk, sk, data):
        try:
            self.table.put_item(Item=self._item(pk, sk, data), ConditionExpression="attribute_not_exists(PK)")
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def update(self, pk, sk, **fields):
        raw = self._raw(pk, sk)
        data = json.loads(raw["d"]) if raw else {}
        data.update(fields)
        self.table.put_item(Item=self._item(pk, sk, data, raw.get("ttl") if raw else None))
        return data

    def incr(self, pk, sk, field, n=1):
        data = self.update(pk, sk)
        data[field] = int(data.get(field, 0)) + n
        self.update(pk, sk, **{field: data[field]})
        return data[field]

    def delete(self, pk, sk):
        self.table.delete_item(Key={"PK": pk, "SK": sk})

    def _query_raw(self, pk, prefix="", desc=False, limit=None, keys_only=False):
        cond = Key("PK").eq(pk) & Key("SK").begins_with(prefix) if prefix else Key("PK").eq(pk)
        args = {"KeyConditionExpression": cond, "ScanIndexForward": not desc}
        if keys_only:
            args["ProjectionExpression"] = "PK, SK"
        out = []
        while True:
            r = self.table.query(**args)
            out.extend(r.get("Items", []))
            if (limit and len(out) >= limit) or "LastEvaluatedKey" not in r:
                return out
            args["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    def query(self, pk, prefix="", desc=False, limit=None):
        rows = [x for x in (self._row(i) for i in self._query_raw(pk, prefix, desc, limit)) if x]
        return rows[:limit] if limit else rows

    def delete_prefix(self, pk, prefix):
        keys = self._query_raw(pk, prefix, keys_only=True)
        with self.table.batch_writer() as bw:
            for k in keys:
                bw.delete_item(Key={"PK": k["PK"], "SK": k["SK"]})
        return len(keys)

    def purge_expired(self):
        return 0  # 交給 DynamoDB TTL
