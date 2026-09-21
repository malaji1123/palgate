import os
import time
from unittest.mock import patch
import pytest
import requests

from palgate_bot import start_http_server


@pytest.fixture(scope="module")
def http_test_server():
    os.environ["API_KEY"] = "test_secret_key"
    test_port = 18088
    server = start_http_server(test_port, host="127.0.0.1")
    time.sleep(0.2)
    base_url = f"http://127.0.0.1:{test_port}"
    yield base_url
    server.shutdown()
    server.server_close()


def test_health_check(http_test_server):
    resp = requests.get(f"{http_test_server}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "palgate_bot"


def test_unauthorized_access(http_test_server):
    # No key
    resp = requests.post(f"{http_test_server}/opengate")
    assert resp.status_code == 401
    assert "Unauthorized" in resp.json()["message"]

    # Wrong key header
    resp = requests.post(f"{http_test_server}/opengate", headers={"X-Api-Key": "wrong_key"})
    assert resp.status_code == 401

    # Wrong key query
    resp = requests.post(f"{http_test_server}/opengate?key=wrong_key")
    assert resp.status_code == 401


def test_authorized_via_header(http_test_server):
    with patch("palgate_bot.open_gate", return_value=(True, "Gate opened!")):
        resp = requests.post(
            f"{http_test_server}/opengate",
            headers={"X-Api-Key": "test_secret_key"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": "Gate opened!"}


def test_authorized_via_bearer(http_test_server):
    with patch("palgate_bot.open_gate", return_value=(True, "Gate opened!")):
        resp = requests.post(
            f"{http_test_server}/opengate",
            headers={"Authorization": "Bearer test_secret_key"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": "Gate opened!"}


def test_authorized_via_query_param(http_test_server):
    with patch("palgate_bot.open_gate", return_value=(True, "Gate opened!")):
        resp = requests.get(f"{http_test_server}/opengate?key=test_secret_key")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": "Gate opened!"}


def test_gate_open_failure_handling(http_test_server):
    with patch("palgate_bot.open_gate", return_value=(False, "Device timeout")):
        resp = requests.post(
            f"{http_test_server}/opengate",
            headers={"X-Api-Key": "test_secret_key"}
        )
        assert resp.status_code == 500
        assert resp.json() == {"status": "failed", "message": "Device timeout"}


def test_not_found_endpoint(http_test_server):
    resp = requests.get(f"{http_test_server}/nonexistent")
    assert resp.status_code == 404
