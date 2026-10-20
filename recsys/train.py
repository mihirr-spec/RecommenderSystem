"""Train, evaluate and export everything the web app needs.

    python -m recsys.train          # full run with hyper-parameter search
    python -m recsys.train --fast   # skip the search, reuse the last tuned params
"""
import argparse
import json
import time

from surprise import SVD

from . import features, pipeline, scoring
from .config import ARTIFACTS_DIR, GENRES, RANDOM_STATE, RELEVANCE_THRESHOLD, TOP_K
from .data import split_per_user

RANKING_METHODS = [
    "Most Popular",
    "SVD (explicit ratings)",
    "Content-based (tags + author)",
    "Item-kNN (implicit co-reading)",
    "Hybrid (tuned)",
]
KNN_NEIGHBOURS = 100


def log(msg, t0):
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def main(fast=False):
    t0 = time.time()
    books, ratings, book_tags = pipeline.load_all()
    n_items = len(books)
    log(f"loaded {len(ratings):,} ratings, {ratings.user_id.nunique():,} users, {n_items:,} books", t0)

    train_df, test_df = split_per_user(ratings)
    trainset, testset = pipeline.to_trainset(train_df), pipeline.to_testset(test_df)
    log(f"split: {len(train_df):,} train / {len(test_df):,} test ratings", t0)

    previous = ARTIFACTS_DIR / "meta.json"
    if fast and previous.exists():
        tuned = json.loads(previous.read_text())["svd_params"]
        tuning_table = json.loads(previous.read_text()).get("svd_tuning", [])
        log(f"reusing tuned SVD params {tuned}", t0)
    else:
        tuned, tuning = pipeline.tune_svd(train_df)
        tuning_table = tuning.to_dict("records")
        log(f"tuned SVD params {tuned}", t0)
        print(tuning.to_string())

    rating_table, fitted = pipeline.benchmark_rating_models(trainset, testset, tuned)
    log("rating-prediction benchmark:\n" + rating_table.to_string(index=False), t0)

    cf = pipeline.export_cf(fitted["SVD (tuned)"], trainset, n_items)
    content, feature_names = features.build_content_matrix(books, book_tags)
    log(f"content matrix {content.shape}, {content.nnz:,} non-zeros", t0)

    knn = scoring.build_item_knn(train_df, n_items, KNN_NEIGHBOURS)
    log(f"item-kNN: {knn.nnz:,} neighbour links", t0)

    ev = pipeline.RankingEvaluator(
        train_df, test_df, cf, knn, content, books["ratings_count"].to_numpy()
    )
    weights, hybrid_grid = ev.tune_hybrid()
    log(f"hybrid weights: {weights}", t0)

    ranking_rows = []
    for m in RANKING_METHODS:
        res = ev.evaluate(ev.test_users, m, weights)
        ranking_rows.append({"model": m, **res})
        log(f"{m}: {res}", t0)

    fold_reg, fold_grid = pipeline.tune_fold_in(cf, train_df, test_df, ev.val_users[:800])
    log(f"cold-start fold-in reg={fold_reg}\n{fold_grid.to_string(index=False)}", t0)

    # Final model: same hyper-parameters, trained on every rating.
    final = SVD(**tuned, random_state=RANDOM_STATE)
    full_trainset = pipeline.to_trainset(ratings)
    final.fit(full_trainset)
    cf_full = pipeline.export_cf(final, full_trainset, n_items)
    knn_full = scoring.build_item_knn(ratings, n_items, KNN_NEIGHBOURS)
    log("final SVD and item-kNN trained on all ratings", t0)

    meta = {
        "dataset": {
            "n_ratings": int(len(ratings)),
            "n_users": int(ratings.user_id.nunique()),
            "n_books": int(n_items),
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
            "sparsity": 1 - len(ratings) / (ratings.user_id.nunique() * n_items),
            "rating_distribution": {
                str(k): int(v)
                for k, v in ratings["rating"].round().astype(int).value_counts().sort_index().items()
            },
            "n_content_features": int(content.shape[1]),
        },
        "svd_params": tuned,
        "svd_tuning": tuning_table,
        "rating_metrics": rating_table.to_dict("records"),
        "ranking_metrics": ranking_rows,
        "ranking_protocol": {
            "k": TOP_K,
            "relevance_threshold": RELEVANCE_THRESHOLD,
            "n_test_users": int(len(ev.test_users)),
            "n_val_users": int(len(ev.val_users)),
        },
        "hybrid": weights,
        "knn_neighbours": KNN_NEIGHBOURS,
        "hybrid_grid": hybrid_grid.to_dict("records"),
        "fold_in": {"reg": fold_reg, "grid": fold_grid.to_dict("records")},
        "genres": list(GENRES),
        "trained_seconds": round(time.time() - t0, 1),
    }
    pipeline.save_artifacts(cf_full, knn_full, content, feature_names, books, ratings, meta)
    log(f"artifacts written to {ARTIFACTS_DIR}", t0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="skip hyper-parameter search")
    main(parser.parse_args().fast)
