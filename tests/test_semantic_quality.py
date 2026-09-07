from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def run_search(query, category):
    response = client.get(
        "/semantic-search",
        params={
            "q": query,
            "category": category,
            "limit": 5,
        },
    )

    assert response.status_code == 200

    return response.json()


def test_semantic_spanish():
    data = run_search(
        "quiero comer ceviche",
        "restaurant",
    )

    assert data["total"] > 0
    assert all(
        item["entity_type"] == "restaurant"
        for item in data["results"]
    )


def test_semantic_english():
    data = run_search(
        "I want a cheap hotel near the beach",
        "hotel",
    )

    assert data["total"] > 0
    assert all(
        item["entity_type"] == "hotel"
        for item in data["results"]
    )


def test_semantic_portuguese():
    data = run_search(
        "quero fazer um passeio de barco",
        "tour",
    )

    assert data["total"] > 0
    assert all(
        item["entity_type"] == "tour"
        for item in data["results"]
    )


def test_semantic_attraction():
    data = run_search(
        "quiero visitar un lugar natural bonito",
        "attraction",
    )

    assert data["total"] > 0
    assert all(
        item["entity_type"] == "attraction"
        for item in data["results"]
    )