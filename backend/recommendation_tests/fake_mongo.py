"""A small in-memory stand-in for the Motor collections these tests touch.

It understands the query and update operators the code under test uses
($in, $nin, $ne, $exists, $regex, $or; $set, $unset, $inc, $setOnInsert) and
nothing more, so a test fails loudly when the code starts relying on another.
"""

import re
from types import SimpleNamespace
from typing import Any, Dict, List


def _matches_value(value: Any, condition: Any) -> bool:
    if isinstance(condition, dict) and any(str(key).startswith("$") for key in condition):
        for op, arg in condition.items():
            if op == "$in" and value not in arg:
                return False
            if op == "$nin" and value in arg:
                return False
            if op == "$ne" and value == arg:
                return False
            if op == "$exists" and (value is not None) != bool(arg):
                return False
            if op == "$regex":
                flags = re.IGNORECASE if "i" in str(condition.get("$options") or "") else 0
                if not isinstance(value, str) or not re.search(arg, value, flags):
                    return False
            if op not in {"$in", "$nin", "$ne", "$exists", "$regex", "$options"}:
                raise NotImplementedError(op)
        return True
    return value == condition


def matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(matches(row, part) for part in condition):
                return False
            continue
        if not _matches_value(row.get(key), condition):
            return False
    return True


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self._rows = self._rows[:count]
        return self

    async def to_list(self, length=None):
        return list(self._rows if length is None else self._rows[:length])

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, rows=()):
        self.rows: List[Dict[str, Any]] = [dict(row) for row in rows]
        self.updates: List[tuple] = []

    def _found(self, query):
        return [row for row in self.rows if matches(row, query)]

    @staticmethod
    def _project(row, projection):
        if not projection:
            return dict(row)
        wanted = {key for key, on in projection.items() if on}
        if not wanted:  # an exclusion-only projection
            return {key: value for key, value in row.items() if projection.get(key, 1)}
        return {key: value for key, value in row.items() if key in wanted}

    def find(self, query=None, projection=None):
        return _Cursor([self._project(row, projection) for row in self._found(query or {})])

    async def find_one(self, query=None, projection=None):
        found = self._found(query or {})
        return self._project(found[0], projection) if found else None

    async def count_documents(self, query):
        return len(self._found(query))

    @staticmethod
    def _apply(row, update):
        row.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            row.pop(key, None)
        for key, step in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + step

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))
        found = self._found(query)
        if not found and upsert:
            row = {key: value for key, value in query.items() if not isinstance(value, dict)}
            row.update(update.get("$setOnInsert", {}))
            self.rows.append(row)
            found = [row]
        for row in found[:1]:
            self._apply(row, update)
        return SimpleNamespace(matched_count=len(found[:1]), modified_count=len(found[:1]))

    async def update_many(self, query, update):
        self.updates.append((query, update))
        found = self._found(query)
        for row in found:
            self._apply(row, update)
        return SimpleNamespace(matched_count=len(found), modified_count=len(found))


def fake_db(**collections) -> SimpleNamespace:
    names = ("connections", "history", "media_history", "media_library", "requests", "recommendations",
             "blacklist", "jobs")
    return SimpleNamespace(**{name: FakeCollection(collections.get(name, ())) for name in names})
