def test_register_creates_user(client):
    response = client.post(
        "/api/auth/register",
        json={"name": "Alice", "email": "alice@example.com", "password": "strongpassword1"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_email_rejected(client):
    payload = {"name": "Bob", "email": "bob@example.com", "password": "strongpassword1"}
    client.post("/api/auth/register", json=payload)
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 409


def test_register_weak_password_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={"name": "Weak", "email": "weak@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_login_success_returns_token(client, register_and_login):
    session = register_and_login()
    response = client.post("/api/auth/login", json={"email": session["email"], "password": session["password"]})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_wrong_password_rejected(client, register_and_login):
    session = register_and_login()
    response = client.post("/api/auth/login", json={"email": session["email"], "password": "wrongpassword"})
    assert response.status_code == 401


def test_login_unknown_email_rejected(client):
    response = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert response.status_code == 401


def test_me_requires_authentication(client):
    response = client.get("/api/users/me")
    assert response.status_code in (401, 403)


def test_me_returns_current_user(client, register_and_login):
    session = register_and_login()
    response = client.get("/api/users/me", headers=session["headers"])
    assert response.status_code == 200
    assert response.json()["email"] == session["email"]
