import asyncio
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sniper import vision
from sniper.app import build_app
from sniper.db import DB

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
B64 = base64.b64encode(JPEG).decode()


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_SECRET_FILE", str(tmp_path / "secret"))
    c = TestClient(build_app(db=DB(str(tmp_path / "s.db")), background=False))
    assert c.post("/api/auth/signup", json={"email": "a@example.com", "password": "hunter2hunter2"}).status_code == 200
    return c


def test_decode_image_checks_type_size_and_content():
    assert vision.decode_image(B64, "image/jpeg") == JPEG
    assert vision.decode_image("data:image/jpeg;base64," + B64, "image/jpeg") == JPEG
    for data, mt, msg in [(B64, "image/heic", "JPEG, PNG"), ("not base64!!", "image/jpeg", "didn't come through"),
                          (base64.b64encode(b"GIF89a....").decode(), "image/jpeg", "isn't a photo"),
                          (base64.b64encode(b"RIFF\x00\x00\x00\x00WAVE").decode(), "image/webp", "isn't a photo"),
                          (base64.b64encode(b"\xff\xd8\xff" + b"\x00" * vision.MAX_IMAGE_BYTES).decode(), "image/jpeg", "too big")]:
        with pytest.raises(ValueError, match=msg):
            vision.decode_image(data, mt)


def test_snap_without_key_gives_labelled_samples(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    item = c.post("/api/snap", json={"image": B64, "mode": "item"}).json()
    assert item["demo"] and item["where_to_find"] and item["search_query"]
    food = c.post("/api/snap", json={"image": B64, "mode": "food"}).json()
    assert food["demo"] and food["calories"] > 0 and food["protein_g"] > 0
    assert c.post("/api/snap", json={"image": B64, "mode": "song"}).status_code == 422
    assert c.post("/api/snap", json={"image": "AAAA", "mode": "item"}).status_code == 422
    assert TestClient(c.app).post("/api/snap", json={"image": B64}).status_code == 401


class _FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed, self.stop_reason, self.calls = parsed, stop_reason, []

    async def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.parsed)


def test_analyze_sends_photo_and_schema_and_handles_refusal():
    msgs = _FakeMessages(vision.FoodResult.model_validate(vision.SAMPLE_FOOD))
    fake = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    out = asyncio.run(vision.analyze(JPEG, "image/jpeg", "food", client=fake))
    assert out["demo"] is False and out["calories"] == vision.SAMPLE_FOOD["calories"]
    kw = msgs.calls[0]
    assert kw["output_format"] is vision.FoodResult and kw["model"] == vision.MODEL
    img = kw["messages"][0]["content"][0]
    assert img["source"]["data"] == B64 and img["source"]["media_type"] == "image/jpeg"
    refused = SimpleNamespace(beta=SimpleNamespace(messages=_FakeMessages(None, "refusal")))
    with pytest.raises(vision.VisionError):
        asyncio.run(vision.analyze(JPEG, "image/jpeg", "item", client=refused))


def test_snap_daily_limit_counts_only_live_photos(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("SNIPER_VISION_DAILY", "2")

    async def fake_analyze(raw, mt, mode):
        return {**vision.SAMPLE_ITEM, "demo": False}
    monkeypatch.setattr(vision, "analyze", fake_analyze)
    assert [c.post("/api/snap", json={"image": B64}).status_code for _ in range(3)] == [200, 200, 429]

    async def broken(raw, mt, mode):
        raise vision.VisionError("The photo service had a problem, try again")
    monkeypatch.setenv("SNIPER_VISION_DAILY", "10")
    monkeypatch.setattr(vision, "analyze", broken)
    r = c.post("/api/snap", json={"image": B64})
    assert r.status_code == 502 and "try again" in r.json()["detail"]


def test_meal_plan_crud_goals_and_privacy(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    steak = {"day": "2026-10-22", "slot": "dinner", "name": "  Ribeye   steak ", "calories": 780, "protein_g": 62, "servings": 1.5}
    mid = c.post("/api/meals", json=steak).json()["id"]
    c.post("/api/meals", json={**steak, "day": "2026-10-30", "name": "Next week"})
    week = c.get("/api/meals?start=2026-10-20&days=7").json()
    assert [m["name"] for m in week["meals"]] == ["Ribeye steak"]
    assert week["meals"][0]["servings"] == 1.5 and week["meals"][0]["eaten"] is False
    assert week["goals"] == {"calories": None, "protein_g": None}

    assert c.put(f"/api/meals/{mid}", json={**steak, "eaten": True}).status_code == 200
    assert c.get("/api/meals?start=2026-10-22&days=1").json()["meals"][0]["eaten"] is True
    assert c.put("/api/meal-goals", json={"calories": 2200, "protein_g": 160}).status_code == 200
    assert c.get("/api/meals?start=2026-10-22").json()["goals"] == {"calories": 2200, "protein_g": 160}

    for bad in [{**steak, "slot": "brunch"}, {**steak, "name": "   "}, {**steak, "calories": -1}, {**steak, "day": "soon"},
                {**steak, "servings": 0}]:
        assert c.post("/api/meals", json=bad).status_code == 422, bad

    other = TestClient(c.app)
    other.post("/api/auth/signup", json={"email": "b@example.com", "password": "hunter2hunter2"})
    assert other.get("/api/meals?start=2026-10-20").json()["meals"] == []
    assert other.delete(f"/api/meals/{mid}").status_code == 404
    assert other.put(f"/api/meals/{mid}", json=steak).status_code == 404

    assert c.delete(f"/api/meals/{mid}").status_code == 200
    assert c.delete(f"/api/meals/{mid}").status_code == 404


def test_deleting_account_removes_meals_and_goals(tmp_path):
    db = DB(str(tmp_path / "d.db"))
    u = db.create_user("x@example.com", "hunter2hunter2")
    db.add_meal(u.id, {"day": "2026-10-22", "slot": "lunch", "name": "Eggs", "servings": 1, "calories": 150,
                       "protein_g": 12, "carbs_g": None, "fat_g": None, "eaten": False})
    db.set_meal_goals(u.id, {"calories": 2000, "protein_g": 150})
    db.delete_user(u.id)
    assert db.count_meals(u.id) == 0 and db.get_setting(f"meal_goals:{u.id}") is None
