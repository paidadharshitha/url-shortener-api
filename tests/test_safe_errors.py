import json

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("field", ["url", "title", "alias", "expires_at"])
def test_malformed_unicode_link_input_is_rejected_without_echo(client: TestClient, field: str):
    payload = {"url": "https://example.com/", field: "broken\ud800text"}
    response = client.post(
        "/links", content=json.dumps(payload), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
    assert client.get("/links").json()["total"] == 0


def test_auth_validation_does_not_reflect_submitted_password(client: TestClient):
    response = client.post("/auth/login", json={"username": "test-user", "password": "s3cr3t"})
    assert response.status_code == 422
    assert "s3cr3t" not in response.text
    assert response.headers["cache-control"] == "no-store"
