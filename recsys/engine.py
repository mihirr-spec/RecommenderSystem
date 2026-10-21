"""The live recommender used by the web app. Loads the artifacts written by
`python -m recsys.train`; does not depend on Surprise."""
import json

import numpy as np
import pandas as pd
from scipy import sparse

from . import data, scoring
from .config import ARTIFACTS_DIR, GENRES, RELEVANCE_THRESHOLD

TO_READ_WEIGHT = 1.0  # a book on the to-read shelf counts like a 3.5-star rating
NEW_USER_CONFIDENCE = 3  # ratings needed before the SVD rating model gets half its weight


class Recommender:
    def __init__(self, artifacts_dir=ARTIFACTS_DIR):
        cf = dict(np.load(artifacts_dir / "cf.npz"))
        cf["mu"] = float(cf["mu"])
        self.cf = scoring.prepare_cf(cf)
        self.knn = sparse.load_npz(artifacts_dir / "knn.npz").tocsr()
        self.content = sparse.load_npz(artifacts_dir / "content.npz").tocsr()
        self.catalog = pd.read_pickle(artifacts_dir / "catalog.pkl")
        self.meta = json.loads((artifacts_dir / "meta.json").read_text(encoding="utf-8"))
        self.feature_names = json.loads(
            (artifacts_dir / "content_features.json").read_text(encoding="utf-8")
        )
        self.n = len(self.catalog)
        self.weights = self.meta["hybrid"]
        self.fold_reg = self.meta["fold_in"]["reg"]

        self.ratings = data.load_ratings().sort_values(["user_id", "rating"], ascending=[True, False])
        self.to_read = data.load_to_read().sort_values("user_id")
        self._r_users = self.ratings["user_id"].to_numpy()
        self._t_users = self.to_read["user_id"].to_numpy()

        cat = self.catalog
        # Popularity uses Goodreads-wide counts: the dataset itself was sampled
        # to ~100 ratings per book, so its own counts are nearly all equal.
        self.pop = scoring.popularity_scores(cat["ratings_count"].to_numpy())
        # Bayesian average: shrink each book's mean towards the global mean.
        v, r = cat["ratings_count"].to_numpy(), cat["average_rating"].to_numpy()
        m = np.quantile(v, 0.5)
        c = np.average(r, weights=v)
        self.bayes = v / (v + m) * r + m / (v + m) * c
        # How well each book fits each genre: 1 if it is the book's main genre,
        # less for its later genres, 0 otherwise.
        fit_by_rank = (1.0, 0.6, 0.4, 0.3)
        self.genre_fit = {
            g: cat["genres"].apply(lambda gs, g=g: fit_by_rank[gs.index(g)] if g in gs else 0.0).to_numpy()
            for g in GENRES
        }
        self.genre_mask = {g: f > 0 for g, f in self.genre_fit.items()}
        self.search_text = (cat["title"] + " " + cat["authors"]).str.lower().to_numpy().astype(str)

    # ------------------------------------------------------------ helpers
    def book(self, idx, **extra):
        b = self.catalog.iloc[int(idx)]
        out = {
            "id": int(b["book_id"]),
            "title": b["title"],
            "authors": b["authors"],
            "year": None if pd.isna(b["year"]) else int(b["year"]),
            "cover": b["image_url"] if b["has_cover"] else None,
            "avg_rating": float(b["average_rating"]),
            "ratings_count": int(b["ratings_count"]),
            "genres": list(b["genres"]),
            "tags": list(b["tags"])[:6],
            "goodreads_url": f"https://www.goodreads.com/book/show/{int(b['goodreads_book_id'])}",
        }
        out.update(extra)
        return out

    def _slice(self, sorted_users, uid):
        lo, hi = np.searchsorted(sorted_users, [uid, uid + 1])
        return lo, hi

    def user_ratings(self, uid):
        lo, hi = self._slice(self._r_users, uid)
        return self.ratings.iloc[lo:hi]

    def user_to_read(self, uid):
        lo, hi = self._slice(self._t_users, uid)
        return self.to_read.iloc[lo:hi]

    def has_user(self, uid):
        return uid in self.cf["user_row"]

    def _row(self, idx, weights):
        """A reader as a sparse 1 x n_books row of taste weights."""
        return sparse.csr_matrix(
            (np.asarray(weights, dtype=np.float32), (np.zeros(len(idx), dtype=int), np.asarray(idx))),
            shape=(1, self.n),
        )

    def _weights(self, alpha, svd_weight, pop_weight):
        w = dict(self.weights)
        if alpha is not None:
            w["alpha"] = alpha
        if svd_weight is not None:
            w["svd_weight"] = svd_weight
        if pop_weight is not None:
            w["pop_weight"] = pop_weight
        return w

    def _recommend(self, row, svd_s, weights, exclude, n, genre=None, boost_genres=(), liked=()):
        knn_s = scoring.knn_scores(self.knn, row)
        cb_s = scoring.content_scores(self.content, scoring.content_profiles(self.content, row))
        hybrid = scoring.hybrid_scores(knn_s, cb_s, svd_s, self.pop, **weights)[0]
        for g in boost_genres:
            hybrid = hybrid + 0.75 * self.genre_fit[g]
        # "Taste match": cosine between the book's tags and the reader's taste
        # profile, relative to the reader's single best-matching book.
        cb = np.clip(cb_s[0], 0, None)
        cb[np.asarray(exclude, dtype=int)] = 0
        taste = cb / (cb.max() + 1e-9)
        if genre:
            hybrid = np.where(self.genre_mask[genre], hybrid, -np.inf)
        recs = scoring.top_n(hybrid, n, exclude)
        reasons = self._explain(recs, np.asarray(liked, dtype=int), row)
        return [
            self.book(
                r,
                predicted_rating=round(float(np.clip(svd_s[0, r], 1, 5)), 2),
                match=int(round(100 * taste[r])),
                reason=reasons.get(int(r)),
            )
            for r in recs
        ]

    def _explain(self, recs, liked_idx, row):
        """Why was each book recommended? The book the reader loved that is most
        similar to it (co-readership + tags), otherwise the tags it shares with
        their taste profile."""
        reasons = {}
        if len(liked_idx):
            co = self.knn[liked_idx][:, recs].toarray()
            co = co / (self.knn[liked_idx].max(axis=1).toarray() + 1e-9)
            cb = (self.content[liked_idx] @ self.content[recs].T).toarray()
            sim = 0.5 * co + 0.5 * cb
            best = sim.argmax(axis=0)
            for i, r in enumerate(recs):
                if sim[best[i], i] > 0.2:
                    because = self.catalog.iloc[int(liked_idx[best[i]])]
                    reasons[int(r)] = {
                        "type": "because",
                        "text": f"Because you liked {because['title']}",
                        "book_id": int(because["book_id"]),
                    }
        profile = scoring.content_profiles(self.content, row)
        prof_tags = set(self.top_profile_tags(profile, 25))
        for r in recs:
            if int(r) in reasons:
                continue
            shared = [t for t in self.catalog.iloc[int(r)]["tags"] if t in prof_tags][:3]
            text = ("Matches your taste for " + ", ".join(shared)) if shared else "Popular with readers like you"
            reasons[int(r)] = {"type": "taste", "text": text}
        return reasons

    def top_profile_tags(self, profile, n=10):
        if profile is None or profile.nnz == 0:
            return []
        arr = profile.toarray().ravel()
        top = np.argsort(-arr)[: n * 3]
        names = self.feature_names
        return [names[i] for i in top if arr[i] > 0 and not names[i].startswith("author:")][:n]

    # ------------------------------------------------------------ public API
    def popular(self, genres=(), sort="popular", n=24, offset=0):
        """Popular or top-rated books, optionally in one or more genres. With
        several genres, books that have all of them come first, then books
        with most of them; within that, books where the genres are their main
        ones rank higher."""
        if isinstance(genres, str):
            genres = [genres]
        score = self.pop if sort == "popular" else self.bayes
        if genres:
            matched = sum(self.genre_mask[g].astype(int) for g in genres)
            fit = sum(self.genre_fit[g] for g in genres)
            score = np.where(matched > 0, 10 * score.std() * matched + 1.5 * score.std() * fit + score, -np.inf)
        order = np.argsort(-score)[offset:offset + n]
        return [self.book(i) for i in order if np.isfinite(score[i])]

    def search(self, q, n=20):
        q = q.strip().lower()
        if not q:
            return []
        hits = np.flatnonzero(np.char.find(self.search_text, q) >= 0)
        hits = hits[np.argsort(-self.pop[hits])][:n]
        return [self.book(i) for i in hits]

    def book_detail(self, book_id, n_similar=12):
        idx = book_id - 1
        b = self.catalog.iloc[idx]
        return self.book(
            idx,
            tags=list(b["tags"]),
            n_ratings_dataset=int(b["n_ratings"]),
            mean_rating_dataset=float(b["mean_rating"]),
            similar=self.similar(book_id, n_similar),
        )

    def similar(self, book_id, n=12, beta=0.5):
        idx = book_id - 1
        sim = scoring.item_similarity(self.knn, self.content, idx, beta)
        recs = scoring.top_n(sim, n, exclude=[idx])
        my_tags = set(self.catalog.iloc[idx]["tags"])
        return [
            self.book(
                r,
                similarity=round(float(sim[r]), 3),
                shared_tags=[t for t in self.catalog.iloc[int(r)]["tags"] if t in my_tags][:3],
            )
            for r in recs
        ]

    def sample_users(self, n=12):
        counts = self.ratings.groupby("user_id").size()
        pool = counts[(counts >= 40) & (counts <= 120)].index.to_numpy()
        pick = np.random.default_rng(7).choice(pool, n, replace=False)
        return [self.user_summary(int(u)) for u in sorted(pick)]

    def user_summary(self, uid):
        r = self.user_ratings(uid)
        liked = r[r["rating"] >= RELEVANCE_THRESHOLD]["book_id"].to_numpy() - 1
        genre_counts = pd.Series(
            [g for i in liked for g in self.catalog.iloc[int(i)]["genres"]], dtype=object
        ).value_counts()
        return {
            "user_id": uid,
            "n_ratings": int(len(r)),
            "avg_rating": round(float(r["rating"].mean()), 2) if len(r) else None,
            "n_to_read": int(len(self.user_to_read(uid))),
            "top_genres": list(genre_counts.index[:3]),
        }

    def _user_row(self, uid):
        r = self.user_ratings(uid)
        t = self.user_to_read(uid)
        idx = np.concatenate([r["book_id"].to_numpy() - 1, t["book_id"].to_numpy() - 1])
        w = np.concatenate([scoring.rating_weights(r["rating"]), np.full(len(t), TO_READ_WEIGHT)])
        # A book can be both rated and on the to-read shelf: keep the rating.
        idx, first = np.unique(idx, return_index=True)
        return r, t, self._row(idx, w[first])

    def user_profile(self, uid):
        r, t, row = self._user_row(uid)
        profile = scoring.content_profiles(self.content, row)
        return {
            **self.user_summary(uid),
            "rated": [self.book(b - 1, user_rating=float(s)) for b, s in zip(r["book_id"], r["rating"])],
            "to_read": [self.book(b - 1) for b in t["book_id"].to_numpy()[:24]],
            "taste_tags": self.top_profile_tags(profile, 12),
            "rating_histogram": {
                str(k): int(v) for k, v in r["rating"].round().astype(int).value_counts().sort_index().items()
            },
        }

    def new_profile(self, rated):
        """Profile of an app user from their saved {book_id: stars} ratings, in
        the same shape as `user_profile`."""
        ids = sorted(rated, key=lambda b: -rated[b])
        stars = np.array([rated[b] for b in ids], dtype=float)
        idx = np.array([b - 1 for b in ids], dtype=int)
        liked = idx[stars >= RELEVANCE_THRESHOLD]
        genre_counts = pd.Series(
            [g for i in liked for g in self.catalog.iloc[int(i)]["genres"]], dtype=object
        ).value_counts()
        profile = scoring.content_profiles(self.content, self._row(idx, scoring.rating_weights(stars))) if len(idx) else None
        return {
            "n_ratings": len(ids),
            "avg_rating": round(float(stars.mean()), 2) if len(ids) else None,
            "n_to_read": 0,
            "top_genres": list(genre_counts.index[:3]),
            "rated": [self.book(b - 1, user_rating=float(rated[b])) for b in ids],
            "to_read": [],
            "taste_tags": self.top_profile_tags(profile, 12),
            "rating_histogram": {str(k): int((np.round(stars) == k).sum()) for k in range(1, 6) if (np.round(stars) == k).any()},
        }

    def _starter_picks(self, genres, n, genre=None, per_author=2):
        """Picks for a reader with no ratings yet: the best books in each chosen
        genre, taking turns between genres, with at most `per_author` books by
        the same author so one series can't fill the list."""
        base = self.bayes + 0.15 * self.pop
        spread = 1.5 * base.std()
        if genre:
            base = np.where(self.genre_mask[genre], base, -np.inf)
        lists = [(None, np.argsort(-base))]
        if genres:
            lists = [
                (g, np.argsort(-np.where(self.genre_mask[g], base + spread * self.genre_fit[g], -np.inf)))
                for g in genres
            ]
        picks, seen, authors = [], set(), {}
        pos = [0] * len(lists)
        while len(picks) < n and any(p < self.n for p in pos):
            for i, (g, order) in enumerate(lists):
                while pos[i] < self.n:
                    r = int(order[pos[i]])
                    pos[i] += 1
                    if g and not self.genre_mask[g][r] or genre and not self.genre_mask[genre][r]:
                        pos[i] = self.n  # this genre's list is used up
                        break
                    author = self.catalog.iloc[r]["authors"].split(",")[0]
                    if r in seen or authors.get(author, 0) >= per_author:
                        continue
                    seen.add(r)
                    authors[author] = authors.get(author, 0) + 1
                    picks.append((r, g))
                    break
                if len(picks) >= n:
                    break
        return [
            self.book(r, match=None, reason={
                "type": "genre", "text": f"Top rated in {g}" if g else "A reader favourite",
            })
            for r, g in picks
        ]

    def recommend_user(self, uid, n=24, alpha=None, pop_weight=None, genre=None, svd_weight=None):
        r, t, row = self._user_row(uid)
        u = self.cf["user_row"][uid]
        svd_s = scoring.cf_scores(self.cf, self.cf["bu"][u], self.cf["pu"][u])
        exclude = np.union1d(r["book_id"].to_numpy() - 1, t["book_id"].to_numpy() - 1)
        liked = r["book_id"].to_numpy()[r["rating"].to_numpy() >= RELEVANCE_THRESHOLD] - 1
        return self._recommend(
            row, svd_s, self._weights(alpha, svd_weight, pop_weight), exclude, n, genre, liked=liked
        )

    def recommend_new(self, rated, genres=(), n=24, alpha=None, pop_weight=None, genre=None, svd_weight=None):
        """Cold start. `rated` maps book_id -> stars for a reader we have never
        seen. With no ratings we fall back to the best books in their chosen
        genres. With ratings, item-kNN and the content model work directly from
        them, and the SVD rating model is folded in (its weight grows with the
        number of ratings)."""
        genres = [g for g in genres if g in self.genre_mask]
        idx = np.array([int(b) - 1 for b in rated], dtype=int)
        stars = np.array(list(rated.values()), dtype=float)

        if len(idx) == 0:
            return self._starter_picks(genres, n, genre)

        bu, pu = scoring.fold_in(self.cf, idx, stars, self.fold_reg)
        svd_s = scoring.cf_scores(self.cf, bu, pu)
        w = self._weights(alpha, svd_weight, pop_weight)
        w["svd_weight"] *= len(idx) / (len(idx) + NEW_USER_CONFIDENCE)
        row = self._row(idx, scoring.rating_weights(stars))
        liked = idx[stars >= RELEVANCE_THRESHOLD]
        return self._recommend(row, svd_s, w, idx, n, genre, genres, liked)

    def stats(self):
        m = dict(self.meta)
        m["hybrid_default"] = self.weights
        return m
