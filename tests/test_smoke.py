from fastapi.testclient import TestClient
import pytest
from app.main import app


@pytest.mark.parametrize('path', ['/health','/destinations','/hotels','/restaurants','/tours','/tour-operators','/attractions','/transport/routes','/services','/search?q=paracas'])
def test_read_endpoint(path):
    with TestClient(app) as client:
        assert client.get(path).status_code == 200


def test_openapi():
    with TestClient(app) as client:
        response = client.get('/openapi.json')
        assert response.status_code == 200
        assert '/refunds' in response.json()['paths']


def test_search_database_error_is_private(monkeypatch):
    from app.routers import search
    from psycopg import OperationalError
    def fail():
        raise OperationalError('private-database-detail')
    monkeypatch.setattr(search,'get_connection',fail)
    response=TestClient(app).get('/search?q=test')
    assert response.status_code==500
    assert 'private-database-detail' not in response.text
