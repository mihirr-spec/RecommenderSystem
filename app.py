"""Folio — book recommender web app.

    python -m recsys.train      # once, to build artifacts/
    python app.py               # then open http://127.0.0.1:8000
"""
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import accounts
from recsys.config import ARTIFACTS_DIR, GENRES
from recsys.engine import Recommender

STATIC = Path(__file__).parent / "static"

if not (ARTIFACTS_DIR / "cf.npz").exists():
    raise SystemExit("No trained model found. Run `python -m recsys.train` first.")

rec = Recommender()
accounts.init_db()
app = FastAPI(title="Folio Book Recommender", version="1.0")

Alpha = Query(None, ge=0, le=1, description="0 = content-based, 1 = collaborative item-kNN")
SvdWeight = Query(None, ge=0, le=2, description="weight of the SVD star-rating model")
PopWeight = Query(None, ge=0, le=2, description="0 = hidden gems, higher = bestsellers")


def check_genre(genre):
    if genre and genre not in GENRES:
        raise HTTPException(400, f"Unknown genre '{genre}'")


def check_book(book_id):
    if not 1 <= book_id <= rec.n:
        raise HTTPException(404, "Book not found")


@app.get("/api/stats")
def stats():
    return rec.stats()


@app.get("/api/genres")
def genres():
    return [{"name": g, "count": int(rec.genre_mask[g].sum())} for g in GENRES]


@app.get("/api/popular")
def popular(
    genre: Optional[str] = None,
    sort: str = Query("popular", pattern="^(popular|top_rated)$"),
    n: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    check_genre(genre)
    return rec.popular(genre, sort, n, offset)


@app.get("/api/search")
def search(q: str = Query(..., min_length=1), n: int = Query(20, ge=1, le=50)):
    return rec.search(q, n)


@app.get("/api/books/{book_id}")
def book(book_id: int):
    check_book(book_id)
    return rec.book_detail(book_id)


@app.get("/api/books/{book_id}/similar")
def similar(book_id: int, n: int = Query(12, ge=1, le=50), beta: float = Query(0.5, ge=0, le=1)):
    check_book(book_id)
    return rec.similar(book_id, n, beta)


@app.get("/api/users/sample")
def sample_users():
    return rec.sample_users()


@app.get("/api/users/{user_id}")
def user(user_id: int):
    if not rec.has_user(user_id):
        raise HTTPException(404, f"User {user_id} not found (valid ids: 1-{len(rec.cf['user_ids'])})")
    return rec.user_profile(user_id)


@app.get("/api/users/{user_id}/recommendations")
def user_recommendations(
    user_id: int,
    n: int = Query(24, ge=1, le=100),
    alpha: Optional[float] = Alpha,
    svd_weight: Optional[float] = SvdWeight,
    pop_weight: Optional[float] = PopWeight,
    genre: Optional[str] = None,
):
    if not rec.has_user(user_id):
        raise HTTPException(404, "User not found")
    check_genre(genre)
    return rec.recommend_user(user_id, n, alpha, pop_weight, genre, svd_weight)


class NewReader(BaseModel):
    ratings: Dict[int, float] = Field(default_factory=dict, description="book_id -> stars (1-5)")
    genres: List[str] = Field(default_factory=list)
    n: int = Field(24, ge=1, le=100)
    alpha: Optional[float] = Field(None, ge=0, le=1)
    svd_weight: Optional[float] = Field(None, ge=0, le=2)
    pop_weight: Optional[float] = Field(None, ge=0, le=2)
    genre: Optional[str] = None


@app.post("/api/recommend")
def recommend_new(body: NewReader):
    for b, s in body.ratings.items():
        check_book(b)
        if not 1 <= s <= 5:
            raise HTTPException(400, "Ratings must be between 1 and 5")
    check_genre(body.genre)
    return rec.recommend_new(
        body.ratings, body.genres, body.n, body.alpha, body.pop_weight, body.genre, body.svd_weight
    )


# ------------------------------------------------------------ accounts
class SignUp(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(..., min_length=6, max_length=128)


class LogIn(BaseModel):
    email: str
    password: str


class Genres(BaseModel):
    genres: List[str]


class Rating(BaseModel):
    rating: float = Field(..., ge=0, le=5)


def current_user(authorization: Optional[str] = Header(None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = accounts.user_for_token(token) if token else None
    if not user:
        raise HTTPException(401, "Please log in")
    return user


@app.get("/api/auth/demo-accounts")
def demo_accounts():
    return [{**d, **rec.user_summary(d["dataset_user_id"])} for d in accounts.DEMO_ACCOUNTS]


@app.post("/api/auth/signup")
def sign_up(body: SignUp):
    try:
        token, user = accounts.sign_up(body.name, body.email, body.password)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"token": token, "user": user}


@app.post("/api/auth/login")
def log_in(body: LogIn):
    result = accounts.log_in(body.email, body.password)
    if not result:
        raise HTTPException(401, "Wrong email or password")
    token, user = result
    return {"token": token, "user": user}


@app.post("/api/auth/demo/{key}")
def log_in_demo(key: str):
    result = accounts.log_in_demo(key)
    if not result:
        raise HTTPException(404, "Unknown demo account")
    token, user = result
    return {"token": token, "user": user}


@app.post("/api/auth/logout")
def log_out(authorization: Optional[str] = Header(None)):
    accounts.log_out((authorization or "").removeprefix("Bearer ").strip())
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    if user["is_demo"]:
        profile = rec.user_profile(user["dataset_user_id"])
    else:
        profile = rec.new_profile(accounts.get_ratings(user["id"]))
    return {**user, "profile": profile}


@app.put("/api/me/genres")
def set_genres(body: Genres, user=Depends(current_user)):
    for g in body.genres:
        check_genre(g)
    accounts.set_genres(user["id"], body.genres)
    return {"genres": body.genres}


@app.put("/api/me/ratings/{book_id}")
def set_rating(book_id: int, body: Rating, user=Depends(current_user)):
    check_book(book_id)
    if user["is_demo"]:
        raise HTTPException(403, "Demo accounts use their ratings from the dataset. Sign up to rate books.")
    if body.rating and body.rating < 1:
        raise HTTPException(400, "Ratings must be between 1 and 5")
    accounts.set_rating(user["id"], book_id, body.rating)
    return {"book_id": book_id, "rating": body.rating}


@app.get("/api/me/recommendations")
def my_recommendations(
    n: int = Query(24, ge=1, le=100),
    alpha: Optional[float] = Alpha,
    svd_weight: Optional[float] = SvdWeight,
    pop_weight: Optional[float] = PopWeight,
    genre: Optional[str] = None,
    user=Depends(current_user),
):
    check_genre(genre)
    if user["is_demo"]:
        return rec.recommend_user(user["dataset_user_id"], n, alpha, pop_weight, genre, svd_weight)
    return rec.recommend_new(
        accounts.get_ratings(user["id"]), user["genres"], n, alpha, pop_weight, genre, svd_weight
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    import sys
    import threading
    import webbrowser

    import uvicorn

    if "--open" in sys.argv:
        threading.Timer(1.5, webbrowser.open, ["http://127.0.0.1:8000"]).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
