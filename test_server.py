import pytest
from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200


def test_register():
    r = client.post("/api/auth/register", json={
        "username": "testuser_" + str(hash(__name__)),
        "password": "testpass123"
    })
    assert r.status_code in (200, 400)


def test_login():
    r = client.post("/api/auth/login", data={
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
