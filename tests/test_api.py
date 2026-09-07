from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert data["database"] is True


def test_destinations():
    response = client.get("/destinations")

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1


def test_hotels():
    response = client.get("/hotels")

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data, dict)
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data

    assert isinstance(data["items"], list)
    assert data["total"] == 82
    assert data["limit"] > 0


def test_semantic_search():
    response = client.get(
        "/semantic-search",
        params={
            "q": "quiero comer ceviche",
            "category": "restaurant",
            "limit": 5,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["query"] == "quiero comer ceviche"
    assert data["category"] == "restaurant"
    assert data["total"] <= 5
    assert isinstance(data["results"], list)