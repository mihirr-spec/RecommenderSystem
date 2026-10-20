"""Smoke tests for the REST API, run against the trained model in artifacts/.

    pytest
"""
import pytest
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

HOBBIT, FELLOWSHIP = 7, 19


def get(url, **params):
    r = client.get(url, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_index_serves_the_ui():
    r = client.get("/")
    assert r.status_code == 200
    assert "Folio" in r.text


def test_stats_include_evaluation_results():
    stats = get("/api/stats")
    assert stats["hybrid_default"]["alpha"] is not None
    assert stats["ranking_metrics"]


def test_popular_and_genre_filter():
    assert len(get("/api/popular", n=5)) == 5
    fantasy = get("/api/popular", genre="Fantasy", n=10)
    assert all("Fantasy" in b["genres"] for b in fantasy)


def test_search_finds_the_hobbit():
    results = get("/api/search", q="hobbit")
    assert results[0]["id"] == HOBBIT


def test_similar_books_to_the_hobbit_include_lotr():
    ids = [b["id"] for b in get(f"/api/books/{HOBBIT}/similar", n=5)]
    assert HOBBIT not in ids
    assert FELLOWSHIP in ids


def test_existing_user_gets_unseen_books_with_reasons():
    profile = get("/api/users/314")
    recs = get("/api/users/314/recommendations", n=10)
    assert len(recs) == 10
    seen = {b["id"] for b in profile["rated"] + profile["to_read"]}
    assert len(seen) > 100
    assert not seen & {b["id"] for b in recs}
    assert all(b["reason"]["text"] for b in recs)


def test_cold_start_with_one_rating():
    r = client.post("/api/recommend", json={"ratings": {str(HOBBIT): 5}, "genres": ["Fantasy"], "n": 5})
    assert r.status_code == 200
    recs = r.json()
    assert HOBBIT not in [b["id"] for b in recs]
    assert any(b["id"] == FELLOWSHIP for b in recs)


def test_cold_start_with_no_ratings_uses_genres():
    r = client.post("/api/recommend", json={"genres": ["Horror"], "n": 5})
    assert r.status_code == 200
    assert all("Horror" in b["genres"] for b in r.json())


@pytest.mark.parametrize("url, code", [
    ("/api/books/0", 404),
    ("/api/books/10001", 404),
    ("/api/users/99999999", 404),
    ("/api/popular?genre=NotAGenre", 400),
])
def test_bad_requests_are_rejected(url, code):
    assert client.get(url).status_code == code


def test_invalid_rating_is_rejected():
    r = client.post("/api/recommend", json={"ratings": {str(HOBBIT): 9}})
    assert r.status_code == 400
