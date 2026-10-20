"""Top-K ranking metrics computed over the FULL catalogue.

For every test user we rank all books they have not rated in training and check
how many of their held-out liked books (rating >= 4) land in the top K. This is
much stricter than the original notebook, which only re-ordered each user's ~4
test books, so its "Recall@10 = 0.82" mostly measured that 4 < 10.
"""
import numpy as np


def rank_top_k(scores, exclude_mask, k):
    scores = np.where(exclude_mask, -np.inf, scores)
    top = np.argpartition(-scores, k, axis=1)[:, :k]
    order = np.argsort(-np.take_along_axis(scores, top, axis=1), axis=1)
    return np.take_along_axis(top, order, axis=1)


def ranking_metrics(top_items, relevant_sets, k):
    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    precision, recall, ndcg, hit = [], [], [], []
    for recs, rel in zip(top_items, relevant_sets):
        hits = np.isin(recs, list(rel))
        n_hit = hits.sum()
        precision.append(n_hit / k)
        recall.append(n_hit / len(rel))
        hit.append(float(n_hit > 0))
        ideal = discounts[: min(len(rel), k)].sum()
        ndcg.append((hits * discounts).sum() / ideal)
    return {
        f"Precision@{k}": float(np.mean(precision)),
        f"Recall@{k}": float(np.mean(recall)),
        f"NDCG@{k}": float(np.mean(ndcg)),
        f"HitRate@{k}": float(np.mean(hit)),
    }


def beyond_accuracy(all_top_items, item_popularity, n_items):
    """Coverage: share of the catalogue that is ever recommended.
    Novelty: mean popularity percentile of recommended books (lower = less mainstream)."""
    flat = np.concatenate(all_top_items)
    pop_rank = item_popularity.argsort().argsort() / (n_items - 1)
    return {
        "Coverage": float(len(np.unique(flat)) / n_items),
        "AvgPopularityPct": float(pop_rank[flat].mean()),
    }
