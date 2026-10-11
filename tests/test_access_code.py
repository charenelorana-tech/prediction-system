import app as app_module
from werkzeug.security import check_password_hash, generate_password_hash


def _csrf_token(client):
    client.get("/change-password")
    with client.session_transaction() as session:
        return session["_csrf_token"]


def test_login_uses_legacy_code_when_access_settings_row_is_absent(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(app_module, "LOGIN_PASSWORD", "legacy-code")
    monkeypatch.setattr(app_module, "load_access_code_hash", lambda: None)
    client = app_module.app.test_client()

    response = client.post("/", data={"password": "legacy-code"})

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["logged_in"] is True


def test_login_verifies_saved_access_code_hash(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(
        app_module,
        "load_access_code_hash",
        lambda: generate_password_hash("updated-code"),
    )
    client = app_module.app.test_client()

    response = client.post("/", data={"password": "updated-code"})

    assert response.status_code == 302


def test_change_password_saves_a_hash(monkeypatch):
    saved_hashes = []
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(
        app_module,
        "verify_access_code",
        lambda code: code == "current-code",
    )
    monkeypatch.setattr(app_module, "save_access_code_hash", saved_hashes.append)
    client = app_module.app.test_client()
    token = _csrf_token(client)

    response = client.post(
        "/change-password",
        data={
            "csrf_token": token,
            "current_code": "current-code",
            "new_code": "updated-code",
            "confirm_code": "updated-code",
        },
    )

    assert response.status_code == 200
    assert b"Access code updated successfully." in response.data
    assert len(saved_hashes) == 1
    assert check_password_hash(saved_hashes[0], "updated-code")


def test_change_password_validates_current_and_new_codes(monkeypatch):
    monkeypatch.setattr(app_module, "USE_MYSQL", True)
    monkeypatch.setattr(
        app_module,
        "verify_access_code",
        lambda code: code == "current-code",
    )
    monkeypatch.setattr(app_module, "save_access_code_hash", lambda code_hash: None)
    client = app_module.app.test_client()
    token = _csrf_token(client)
    cases = (
        ("wrong-current", "updated-code", "updated-code", "current access code is incorrect"),
        ("current-code", "short", "short", "at least 6 characters"),
        ("current-code", "updated-code", "different-code", "do not match"),
        ("current-code", "current-code", "current-code", "must differ"),
    )

    for current, new, confirm, expected_message in cases:
        response = client.post(
            "/change-password",
            data={
                "csrf_token": token,
                "current_code": current,
                "new_code": new,
                "confirm_code": confirm,
            },
        )
        assert response.status_code == 200
        assert expected_message.encode() in response.data


def test_change_password_is_accessible_without_login():
    response = app_module.app.test_client().get("/change-password")

    assert response.status_code == 200
    assert b'name="current_code"' in response.data
    assert b'name="new_code"' in response.data
    assert b'name="confirm_code"' in response.data
