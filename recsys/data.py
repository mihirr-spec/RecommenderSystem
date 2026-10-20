"""Loading and cleaning the Goodbooks-10k files.

ID convention (this was the bug in the original notebook):
    ratings.csv / to_read.csv  `book_id`  ==  books.csv `id`   (1..10000)
    book_tags.csv `goodreads_book_id`  ==  books.csv `book_id` (Goodreads ID)
Here books.csv `id` is renamed to `book_id` and the Goodreads ID becomes
`goodreads_book_id`, so every table joins on the same `book_id`.
The internal row index of a book is always `book_id - 1`.
"""
import re

import numpy as np
import pandas as pd

from .config import DATA_DIR, GENRES, NOISE_TAG, RANDOM_STATE


def load_ratings(data_dir=DATA_DIR):
    ratings = pd.read_csv(data_dir / "ratings.csv")
    ratings = ratings.dropna(subset=["user_id", "book_id", "rating"])
    # 2,278 (user, book) pairs are duplicated in the raw file; average them.
    ratings = (
        ratings.groupby(["user_id", "book_id"], as_index=False)["rating"].mean()
    )
    return ratings


def load_to_read(data_dir=DATA_DIR):
    return pd.read_csv(data_dir / "to_read.csv").drop_duplicates()


def load_books(data_dir=DATA_DIR):
    books = pd.read_csv(data_dir / "books.csv")
    books = books.rename(columns={"book_id": "goodreads_book_id"})
    books = books.rename(columns={"id": "book_id"})
    books = books.sort_values("book_id").reset_index(drop=True)
    assert (books["book_id"].to_numpy() == np.arange(1, len(books) + 1)).all()

    books["title"] = books["title"].fillna(books["original_title"]).fillna("Untitled")
    books["authors"] = books["authors"].fillna("Unknown")
    books["year"] = books["original_publication_year"].astype("Int64")
    books["has_cover"] = ~books["image_url"].str.contains("nophoto", na=True)
    return books


def load_tags(books, data_dir=DATA_DIR):
    """Book-tag counts keyed by our `book_id`, with shelf-style noise tags flagged."""
    book_tags = pd.read_csv(data_dir / "book_tags.csv")
    tags = pd.read_csv(data_dir / "tags.csv")
    book_tags = book_tags[book_tags["count"] > 0]
    book_tags = book_tags.merge(tags, on="tag_id")
    book_tags = book_tags.merge(
        books[["book_id", "goodreads_book_id"]], on="goodreads_book_id"
    )
    book_tags = book_tags.groupby(["book_id", "tag_name"], as_index=False)["count"].sum()
    names = book_tags["tag_name"].astype(str)
    is_noise = {
        t: bool(NOISE_TAG.match(t)) or not re.fullmatch(r"[a-z0-9\-]+", t)
        for t in names.unique()
    }
    book_tags["noise"] = names.map(is_noise)
    return book_tags[["book_id", "tag_name", "count", "noise"]]


def assign_genres(books, book_tags, max_genres=3, min_share=0.15):
    """A book gets a genre when that genre's tags carry >= `min_share` of the
    book's strongest genre signal. Returns a list of genre names per book."""
    tag_to_genre = {t: g for g, ts in GENRES.items() for t in ts}
    gt = book_tags[book_tags["tag_name"].isin(tag_to_genre)].copy()
    gt["genre"] = gt["tag_name"].map(tag_to_genre)
    score = gt.groupby(["book_id", "genre"])["count"].sum().reset_index()
    score["share"] = score["count"] / score.groupby("book_id")["count"].transform("max")
    score = score[score["share"] >= min_share].sort_values(
        ["book_id", "count"], ascending=[True, False]
    )
    genres = score.groupby("book_id")["genre"].apply(lambda s: list(s)[:max_genres])
    return books["book_id"].map(genres).apply(lambda g: g if isinstance(g, list) else [])


def top_tags(book_tags, n=8):
    clean = book_tags[~book_tags["noise"]].sort_values(
        ["book_id", "count"], ascending=[True, False]
    )
    return clean.groupby("book_id")["tag_name"].apply(lambda s: list(s)[:n])


def split_per_user(ratings, test_frac=0.2, min_ratings=5, seed=RANDOM_STATE):
    """Hold out `test_frac` of each user's ratings. Users with fewer than
    `min_ratings` ratings stay entirely in train (too little to evaluate on)."""
    r = ratings.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    n_user = r.groupby("user_id")["user_id"].transform("size")
    rank = r.groupby("user_id").cumcount()
    is_test = (n_user >= min_ratings) & (rank < np.floor(n_user * test_frac))
    return r[~is_test].reset_index(drop=True), r[is_test].reset_index(drop=True)
