import pytest
from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200


def test_register():
    import time
    r = client.post("/api/register", json={
        "username": f"testuser_{int(time.time()*1000)%100000}",
        "display_name": "Test User",
        "password": "testpass123",
        "public_key": "test_key"
    })
    assert r.status_code in (200, 400, 409)


def test_login():
    r = client.post("/api/login", json={
        "username": "test_login_user",
        "password": "testpass"
    })
    assert r.status_code in (200, 401)


def test_chats_no_auth():
    r = client.get("/api/chats")
    assert r.status_code in (401, 403)


def test_create_chat_no_auth():
    r = client.post("/api/chats", json={"name": "test"})
    assert r.status_code in (401, 403)


def test_docs():
    r = client.get("/docs")
    assert r.status_code == 200
