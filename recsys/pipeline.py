"""End-to-end training and evaluation steps. Used by `python -m recsys.train`
and step-by-step by notebooks/book.ipynb."""
import json
import time

import numpy as np
import pandas as pd
from scipy import sparse
from surprise import SVD, BaselineOnly, Dataset, NormalPredictor, Reader, accuracy
from surprise.model_selection import KFold, RandomizedSearchCV

from . import data, evaluation, features, scoring
from .config import ARTIFACTS_DIR, RANDOM_STATE, RELEVANCE_THRESHOLD, TOP_K

READER = Reader(rating_scale=(1, 5))

# The configuration used in the original notebook, kept as a reference point.
ORIGINAL_SVD_PARAMS = dict(n_factors=100, n_epochs=20, lr_all=0.005, reg_all=0.02)


# ---------------------------------------------------------------- data
def load_all():
    books = data.load_books()
    ratings = data.load_ratings()
    book_tags = data.load_tags(books)
    books["genres"] = data.assign_genres(books, book_tags, overrides=data.load_genre_overrides())
    tag_lists = data.top_tags(book_tags)
    books["tags"] = books["book_id"].map(tag_lists).apply(
        lambda t: t if isinstance(t, list) else []
    )
    return books, ratings, book_tags


def to_trainset(df):
    return Dataset.load_from_df(df[["user_id", "book_id", "rating"]], READER).build_full_trainset()


def to_testset(df):
    return list(df[["user_id", "book_id", "rating"]].itertuples(index=False, name=None))


# ---------------------------------------------------------------- rating prediction
def tune_svd(train_df, sample_size=300_000, n_iter=10):
    sample = train_df.sample(min(sample_size, len(train_df)), random_state=RANDOM_STATE)
    dataset = Dataset.load_from_df(sample[["user_id", "book_id", "rating"]], READER)
    search = RandomizedSearchCV(
        SVD,
        {
            "n_factors": [50, 100, 150, 200],
            "n_epochs": [20, 30, 40],
            "lr_all": [0.005, 0.01],
            "reg_all": [0.02, 0.05, 0.08, 0.1],
            "random_state": [RANDOM_STATE],
        },
        n_iter=n_iter,
        measures=["rmse", "mae"],
        cv=KFold(n_splits=3, random_state=RANDOM_STATE, shuffle=True),
        n_jobs=-1,
        random_state=RANDOM_STATE,
    )
    search.fit(dataset)
    results = pd.DataFrame(search.cv_results)
    cols = ["param_n_factors", "param_n_epochs", "param_lr_all", "param_reg_all",
            "mean_test_rmse", "mean_test_mae"]
    results = results[cols].sort_values("mean_test_rmse").reset_index(drop=True)
    best = {k: v for k, v in search.best_params["rmse"].items() if k != "random_state"}
    return best, results


def benchmark_rating_models(trainset, testset, tuned_params):
    """RMSE / MAE on held-out ratings. Lower is better."""
    models = {
        "Random (NormalPredictor)": NormalPredictor(),
        "Baseline (user + item bias)": BaselineOnly(verbose=False),
        "SVD (original notebook params)": SVD(**ORIGINAL_SVD_PARAMS, random_state=RANDOM_STATE),
        "SVD (tuned)": SVD(**tuned_params, random_state=RANDOM_STATE),
    }
    rows, fitted = [], {}
    for name, algo in models.items():
        t0 = time.time()
        algo.fit(trainset)
        preds = algo.test(testset)
        rows.append({
            "model": name,
            "RMSE": accuracy.rmse(preds, verbose=False),
            "MAE": accuracy.mae(preds, verbose=False),
            "train_seconds": round(time.time() - t0, 1),
        })
        fitted[name] = algo
    return pd.DataFrame(rows), fitted


def export_cf(algo, trainset, n_items):
    """Copy the Surprise model into plain numpy arrays in catalogue order
    (row j == book_id j + 1), so the web app does not need Surprise at all."""
    k = algo.qi.shape[1]
    bi = np.zeros(n_items, dtype=np.float32)
    qi = np.zeros((n_items, k), dtype=np.float32)
    for inner in trainset.all_items():
        j = int(trainset.to_raw_iid(inner)) - 1
        bi[j] = algo.bi[inner]
        qi[j] = algo.qi[inner]
    user_ids = np.array([int(trainset.to_raw_uid(u)) for u in trainset.all_users()])
    cf = {
        "mu": np.float32(trainset.global_mean),
        "bi": bi,
        "qi": qi,
        "bu": algo.bu.astype(np.float32),
        "pu": algo.pu.astype(np.float32),
        "user_ids": user_ids,
    }
    return scoring.prepare_cf(cf)


# ---------------------------------------------------------------- ranking evaluation
class RankingEvaluator:
    """Computes every method's scores for a batch of held-out users."""

    def __init__(self, train_df, test_df, cf, knn, content, goodreads_counts, n_val=1500, n_test=3000):
        n_items = len(goodreads_counts)
        self.n_items = n_items
        self.cf, self.knn, self.content = cf, knn, content

        rows = train_df["user_id"].map(cf["user_row"]).to_numpy()
        cols = train_df["book_id"].to_numpy() - 1
        shape = (len(cf["user_ids"]), n_items)
        self.seen = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=shape)
        self.weights = sparse.csr_matrix(
            (scoring.rating_weights(train_df["rating"]), (rows, cols)), shape=shape
        )
        # Popularity comes from Goodreads-wide rating counts in books.csv: this
        # dataset was sampled to ~100 ratings per book, so counting ratings
        # inside it says nothing about how popular a book really is.
        self.item_counts = np.asarray(goodreads_counts)
        self.pop = scoring.popularity_scores(self.item_counts)

        liked = test_df[test_df["rating"] >= RELEVANCE_THRESHOLD]
        liked = liked[liked["user_id"].isin(cf["user_row"])]
        relevant = liked.groupby("user_id")["book_id"].apply(lambda s: set(s - 1))
        users = relevant.index.to_numpy().copy()
        np.random.default_rng(RANDOM_STATE).shuffle(users)
        self.val_users = users[:n_val]
        self.test_users = users[n_val:n_val + n_test]
        self.relevant = relevant

    def components(self, users):
        r = np.array([self.cf["user_row"][u] for u in users])
        w = self.weights[r]
        svd_s = scoring.cf_scores(self.cf, self.cf["bu"][r], self.cf["pu"][r])
        knn_s = scoring.knn_scores(self.knn, w)
        cb_s = scoring.content_scores(self.content, scoring.content_profiles(self.content, w))
        pop_s = np.broadcast_to(self.pop, svd_s.shape)
        exclude = self.seen[r].toarray() > 0
        return {"knn": knn_s, "cb": cb_s, "svd": svd_s, "pop": pop_s, "exclude": exclude}

    def score(self, method, c, weights):
        single = {
            "Most Popular": "pop",
            "SVD (explicit ratings)": "svd",
            "Item-kNN (implicit co-reading)": "knn",
            "Content-based (tags + author)": "cb",
        }
        if method in single:
            return c[single[method]]
        return scoring.hybrid_scores(c["knn"], c["cb"], c["svd"], c["pop"], **weights)

    def evaluate(self, users, method, weights=None, batch=500):
        tops = []
        for i in range(0, len(users), batch):
            c = self.components(users[i:i + batch])
            tops.append(evaluation.rank_top_k(self.score(method, c, weights), c["exclude"], TOP_K))
        tops = np.vstack(tops)
        rel = [self.relevant[u] for u in users]
        out = evaluation.ranking_metrics(tops, rel, TOP_K)
        out.update(evaluation.beyond_accuracy(list(tops), self.item_counts, self.n_items))
        return out

    def tune_hybrid(
        self,
        alphas=(0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0),
        svd_weights=(0.0, 0.1, 0.25),
        pop_weights=(0.0, 0.1, 0.25, 0.5),
    ):
        """Grid-search the blend weights on validation users (disjoint from the
        users we report final numbers on)."""
        c = self.components(self.val_users)
        rel = [self.relevant[u] for u in self.val_users]
        rows = []
        for a in alphas:
            for sw in svd_weights:
                for pw in pop_weights:
                    s = scoring.hybrid_scores(c["knn"], c["cb"], c["svd"], c["pop"], a, sw, pw)
                    top = evaluation.rank_top_k(s, c["exclude"], TOP_K)
                    m = evaluation.ranking_metrics(top, rel, TOP_K)
                    rows.append({"alpha": a, "svd_weight": sw, "pop_weight": pw, **m})
        grid = pd.DataFrame(rows).sort_values(f"NDCG@{TOP_K}", ascending=False)
        best = grid.iloc[0]
        weights = {k: float(best[k]) for k in ("alpha", "svd_weight", "pop_weight")}
        return weights, grid.reset_index(drop=True)


def tune_fold_in(cf, train_df, test_df, users, regs=(0.1, 0.5, 1, 2, 5, 10, 20)):
    """Pick the ridge strength for cold-start fold-in by pretending some known
    users are new: re-estimate their vectors from training ratings only and
    measure RMSE on their held-out ratings."""
    tr = train_df[train_df["user_id"].isin(users)].groupby("user_id")
    te = test_df[test_df["user_id"].isin(users)].groupby("user_id")
    rows = []
    for reg in regs:
        err = []
        for u, g in tr:
            if u not in te.groups:
                continue
            bu, pu = scoring.fold_in(cf, g["book_id"].to_numpy() - 1, g["rating"].to_numpy(), reg)
            t = te.get_group(u)
            pred = np.clip(scoring.cf_scores(cf, bu, pu)[0, t["book_id"].to_numpy() - 1], 1, 5)
            err.append((pred - t["rating"].to_numpy()) ** 2)
        rows.append({"reg": reg, "RMSE": float(np.sqrt(np.concatenate(err).mean()))})
    grid = pd.DataFrame(rows)
    return float(grid.loc[grid["RMSE"].idxmin(), "reg"]), grid


# ---------------------------------------------------------------- export
def save_artifacts(cf, knn, content, feature_names, books, ratings, meta):
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    sparse.save_npz(ARTIFACTS_DIR / "knn.npz", knn)
    with open(ARTIFACTS_DIR / "content_features.json", "w", encoding="utf-8") as f:
        json.dump(feature_names, f)
    np.savez_compressed(
        ARTIFACTS_DIR / "cf.npz",
        **{k: cf[k] for k in ["mu", "bi", "qi", "bu", "pu", "user_ids"]},
    )
    sparse.save_npz(ARTIFACTS_DIR / "content.npz", content)

    stats = ratings.groupby("book_id")["rating"].agg(["size", "mean"])
    catalog = books[[
        "book_id", "goodreads_book_id", "title", "original_title", "authors", "year",
        "average_rating", "ratings_count", "image_url", "small_image_url", "has_cover",
        "genres", "tags", "language_code",
    ]].copy()
    catalog["n_ratings"] = catalog["book_id"].map(stats["size"]).fillna(0).astype(int)
    catalog["mean_rating"] = catalog["book_id"].map(stats["mean"]).fillna(0).round(2)
    catalog.to_pickle(ARTIFACTS_DIR / "catalog.pkl")

    with open(ARTIFACTS_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=float)
