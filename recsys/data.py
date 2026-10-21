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

from .config import DATA_DIR, FICTION_ONLY, FORM_GENRES, GENRES, NOISE_TAG, NONFICTION_ONLY, RANDOM_STATE


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


def load_genre_overrides(data_dir=DATA_DIR):
    """Genres checked by hand for books whose Goodreads tags are misleading:
    {book_id: [genre, ...]} from data/genre_overrides.csv."""
    path = data_dir / "genre_overrides.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return {int(b): g.split(";") for b, g in zip(df["book_id"], df["genres"])}


def tag_genres(tag_names):
    """Which genres each tag counts for. Tags are matched by whole keywords, so
    "horror-thriller" counts for Horror and Thriller. Combined "sci-fi-fantasy"
    shelves count for neither: they are a bookstore section, not a genre."""
    pats = {g: re.compile(rf"(^|-)({p})(-|$)") for g, p in GENRES.items()}
    out = {}
    for t in tag_names:
        gs = {g for g, p in pats.items() if p.search(t)}
        if {"Fantasy", "Science Fiction"} <= gs:
            gs -= {"Fantasy", "Science Fiction"}
        if "Science Fiction" in gs:
            gs.discard("Science")  # "science-fiction" is not popular science
        if gs:
            out[t] = sorted(gs)
    return out


def assign_genres(books, book_tags, max_genres=4, max_content=3, content_share=0.4, form_share=0.3,
                  overrides=None):
    """Genres for each book from its Goodreads tags.

    * Strength of a genre on a book = its most-used matching tag plus half of
      the other matching tags (synonyms like "sci-fi" / "science-fiction" are
      often the same readers, so they don't count in full).
    * Books tagged "non-fiction" by more readers than "fiction" can't get a
      fiction-only genre, and the other way round.
    * Content genres (Fantasy, Romance, ...) are kept when at least
      `content_share` as strong as the book's top content genre. Form and
      audience genres (Classics, Young Adult, ...) are kept when at least
      `form_share` as strong as the book's strongest genre overall.
    * `overrides` maps book_id -> genres checked by hand; they replace the
      computed genres for those books."""
    clean = book_tags[~book_tags["noise"]]
    t2g = tag_genres(clean["tag_name"].unique())
    gt = clean[clean["tag_name"].isin(t2g)].copy()
    gt["genre"] = gt["tag_name"].map(t2g)
    gt = gt.explode("genre")
    agg = gt.groupby(["book_id", "genre"])["count"].agg(["max", "sum"])
    score = (agg["max"] + 0.5 * (agg["sum"] - agg["max"])).rename("score").reset_index()

    counts = book_tags.pivot_table(index="book_id", columns="tag_name", values="count", aggfunc="max")
    fiction = counts.get("fiction", pd.Series(dtype=float)).reindex(counts.index).fillna(0)
    nonfiction = counts.reindex(columns=["non-fiction", "nonfiction"]).max(axis=1).fillna(0)
    is_nonfiction = score["book_id"].map(nonfiction > fiction).fillna(False).astype(bool)
    is_fiction = score["book_id"].map(fiction > nonfiction).fillna(False).astype(bool)
    allowed = ~((is_nonfiction & score["genre"].isin(FICTION_ONLY)) | (is_fiction & score["genre"].isin(NONFICTION_ONLY)))
    score = score[allowed]

    is_form = score["genre"].isin(FORM_GENRES)
    top_all = score.groupby("book_id")["score"].transform("max")
    top_content = score["score"].where(~is_form).groupby(score["book_id"]).transform("max")
    keep = np.where(is_form, score["score"] >= form_share * top_all, score["score"] >= content_share * top_content)
    score = score[keep].sort_values(["book_id", "score"], ascending=[True, False])

    def pick(rows):
        content = [g for g in rows if g not in FORM_GENRES][:max_content]
        chosen = [g for g in rows if g in content or g in FORM_GENRES]
        return chosen[:max_genres]

    genres = score.groupby("book_id")["genre"].apply(lambda s: pick(list(s)))
    out = books["book_id"].map(genres).apply(lambda g: g if isinstance(g, list) else [])
    if overrides:
        out = pd.Series([overrides.get(b, g) for b, g in zip(books["book_id"], out)], index=books.index)
    return out


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
