import json
from types import SimpleNamespace

import pytest

from telegram_mcp.tools import groups


class FakeParticipantsClient:
    """Client stub whose iter_participants mirrors Telethon 1.42's signature.

    Telethon's ChatMethods.iter_participants(entity, limit=None, *, search='',
    filter=None, aggressive=False) has NO 'offset' parameter, so passing one
    raises TypeError — exactly the regression this test guards against
    (GEN-ERR-806 on get_participants).
    """

    def __init__(self, users):
        self.users = users
        self.calls = []

    def iter_participants(self, entity, limit=None, *, search="", filter=None, aggressive=False):
        self.calls.append({"entity": entity, "limit": limit})
        selected = self.users if limit is None else self.users[: int(limit)]

        async def _gen():
            for user in selected:
                yield user

        return _gen()


def _users(count):
    return [
        SimpleNamespace(id=1000 + i, first_name="User", last_name=str(i)) for i in range(count)
    ]


async def _noop_ensure_connected(cl=None):
    return None


def _patch_runtime(monkeypatch, client):
    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "ensure_connected", _noop_ensure_connected)


def _parse_results(result):
    payload = json.loads(result.split("\n\n")[0])
    return payload["results"]


@pytest.mark.asyncio
async def test_get_participants_first_page_does_not_pass_offset_kwarg(monkeypatch):
    client = FakeParticipantsClient(_users(3))
    _patch_runtime(monkeypatch, client)

    result = await groups.get_participants(chat_id=-1003432012873, page=1, page_size=50)

    assert "GEN-ERR" not in result
    records = _parse_results(result)
    assert [r["id"] for r in records] == [1000, 1001, 1002]
    assert client.calls == [{"entity": -1003432012873, "limit": 50}]
    assert "Page 1 (showing 3 participants)" in result


@pytest.mark.asyncio
async def test_get_participants_second_page_slices_locally(monkeypatch):
    client = FakeParticipantsClient(_users(5))
    _patch_runtime(monkeypatch, client)

    result = await groups.get_participants(chat_id=-1003432012873, page=2, page_size=2)

    assert "GEN-ERR" not in result
    records = _parse_results(result)
    assert [r["id"] for r in records] == [1002, 1003]
    # Fetch covers offset + page_size, page is sliced out locally
    assert client.calls == [{"entity": -1003432012873, "limit": 4}]
    assert "Page 2 (showing 2 participants)" in result
    assert "more results available on page 3" in result


@pytest.mark.asyncio
async def test_get_participants_page_past_end_returns_empty(monkeypatch):
    client = FakeParticipantsClient(_users(3))
    _patch_runtime(monkeypatch, client)

    result = await groups.get_participants(chat_id=-1003432012873, page=3, page_size=50)

    assert "GEN-ERR" not in result
    assert _parse_results(result) == []
