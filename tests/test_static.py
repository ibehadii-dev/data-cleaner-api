"""Tests for the single-file frontend served at / and /static."""


def test_index_serves_frontend(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "data-cleaner-api" in response.text
    assert "Load Messy Sample" in response.text
    assert 'id="demoBtn"' in response.text
    assert "Run Server Demo (/demo/messy-leads)" in response.text
    assert 'id="processingTime"' in response.text
    assert 'id="preset"' in response.text
    assert 'id="phoneColumns"' in response.text
    assert 'id="countryCode"' in response.text
    assert 'id="zipBtn"' in response.text
    assert "Download ZIP Bundle" in response.text
    assert 'id="csvText"' in response.text


def test_static_mount_serves_index(client) -> None:
    response = client.get("/static/index.html")
    assert response.status_code == 200
    assert "Data Cleaner" in response.text


def test_existing_documentation_routes_are_unaffected(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
