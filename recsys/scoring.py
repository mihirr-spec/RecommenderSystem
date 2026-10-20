"""Scoring functions shared by offline evaluation and the live web app.

All functions return scores over the full catalogue (one column per book,
column j == book_id j + 1), so they can be blended and ranked the same way.
"""
import numpy as np
from scipy import sparse


def zscore(scores):
    """Standardise each row so different signals can be added together."""
    scores = np.atleast_2d(scores)
    mean = scores.mean(axis=1, keepdims=True)
    std = scores.std(axis=1, keepdims=True) + 1e-9
    return (scores - mean) / std


# ---------------------------------------------------------------- collaborative
def prepare_cf(cf):
    """Add lookups derived from the exported SVD arrays."""
    cf["user_row"] = {int(u): i for i, u in enumerate(cf["user_ids"])}
    return cf


def cf_scores(cf, bu, pu):
    """Predicted rating r = mu + b_u + b_i + p_u . q_i  for every book.
    `bu` is (B,) and `pu` is (B, k) for a batch of B users."""
    bu = np.atleast_1d(bu)[:, None]
    pu = np.atleast_2d(pu)
    return cf["mu"] + bu + cf["bi"][None, :] + pu @ cf["qi"].T


def fold_in(cf, item_idx, ratings, reg):
    """Estimate (b_u, p_u) for a user who was NOT in training, from a handful of
    ratings, by solving the ridge regression the SVD model would have solved
    with the item factors held fixed. This is how new readers get personalised
    collaborative-filtering recommendations without retraining."""
    item_idx = np.asarray(item_idx)
    ratings = np.asarray(ratings, dtype=float)
    k = cf["qi"].shape[1]
    if len(item_idx) == 0:
        return 0.0, np.zeros(k)
    X = np.hstack([np.ones((len(item_idx), 1)), cf["qi"][item_idx]])
    y = ratings - cf["mu"] - cf["bi"][item_idx]
    A = X.T @ X + reg * np.eye(k + 1)
    w = np.linalg.solve(A, X.T @ y)
    return w[0], w[1:]


# ---------------------------------------------------------------- item-kNN (implicit)
def build_item_knn(train_df, n_items, k=100):
    """Item-item cosine similarity on the implicit "who rated what" matrix,
    keeping the k strongest neighbours of each book.

    sim(i, j) = |readers of i and j| / sqrt(|readers of i| * |readers of j|)

    This captures "people who read X also read Y", which predicts what a reader
    picks up next far better than a model trained to predict star values.
    """
    users, rows = np.unique(train_df["user_id"].to_numpy(), return_inverse=True)
    cols = train_df["book_id"].to_numpy() - 1
    R = sparse.csr_matrix(
        (np.ones(len(cols), dtype=np.float32), (rows, cols)), shape=(len(users), n_items)
    )
    inv_norm = sparse.diags(1.0 / (np.sqrt(np.asarray(R.sum(axis=0)).ravel()) + 1e-9))
    sim = (inv_norm @ (R.T @ R) @ inv_norm).tocsr()
    sim.setdiag(0)
    sim.eliminate_zeros()

    data, indices, indptr = [], [], [0]
    for i in range(n_items):
        a, b = sim.indptr[i], sim.indptr[i + 1]
        d, j = sim.data[a:b], sim.indices[a:b]
        if len(d) > k:
            keep = np.argpartition(-d, k)[:k]
            d, j = d[keep], j[keep]
        data.append(d)
        indices.append(j)
        indptr.append(indptr[-1] + len(d))
    return sparse.csr_matrix(
        (np.concatenate(data).astype(np.float32), np.concatenate(indices), indptr),
        shape=(n_items, n_items),
    )


def knn_scores(knn, interactions):
    """Each book the reader rated votes for its neighbours, weighted by how
    much they liked it. `interactions` is (B x n_books) sparse."""
    return (sparse.csr_matrix(interactions) @ knn).toarray()


# ---------------------------------------------------------------- content-based
def rating_weights(ratings):
    """Map stars to taste-profile weights, following Goodreads' own scale
    (3 = "liked it", 2 = "it was ok", 1 = "did not like it"):
    5 -> +2.5, 4 -> +1.5, 3 -> +0.5, 2 -> -0.5, 1 -> -1.5."""
    return np.asarray(ratings, dtype=float) - 2.5


def content_profiles(content, interactions):
    """`interactions` is a sparse (B x n_books) matrix of weights.
    Returns the (B x n_features) taste profile of each user."""
    profiles = interactions @ content
    return sparse.csr_matrix(profiles)


def content_scores(content, profiles):
    """Cosine-style similarity of every book to each user's taste profile."""
    profiles = sparse.csr_matrix(profiles)
    norms = np.sqrt(profiles.multiply(profiles).sum(axis=1)).A1 + 1e-9
    scores = (profiles @ content.T).toarray()
    return scores / norms[:, None]


# ---------------------------------------------------------------- popularity
def popularity_scores(rating_counts):
    return np.log1p(np.asarray(rating_counts, dtype=float))


# ---------------------------------------------------------------- hybrid
def hybrid_scores(knn_s, cb_s, svd_s, pop_s, alpha, svd_weight, pop_weight):
    """alpha in [0, 1] slides from pure content (0) to pure collaborative
    item-kNN (1). svd_weight mixes in the explicit SVD rating prediction and
    pop_weight a popularity prior (0 = hidden gems, higher = bestsellers)."""
    s = alpha * zscore(knn_s) + (1 - alpha) * zscore(cb_s)
    if svd_weight:
        s = s + svd_weight * zscore(svd_s)
    if pop_weight:
        s = s + pop_weight * zscore(pop_s)
    return s


# ---------------------------------------------------------------- item-item
def item_similarity(knn, content, item, beta=0.5):
    """Similarity of one book to every other: a blend of co-readership
    ("readers of X also read Y") and tag/author similarity ("Y is described
    like X"), each scaled to a maximum of 1."""
    co = knn[item].toarray().ravel()
    cb = (content @ content[item].T).toarray().ravel()
    cb[item] = 0
    return beta * co / (co.max() + 1e-9) + (1 - beta) * cb / (cb.max() + 1e-9)


def top_n(scores, n, exclude=()):
    scores = np.array(scores, dtype=float, copy=True).ravel()
    if len(exclude):
        scores[np.asarray(list(exclude), dtype=int)] = -np.inf
    idx = np.argpartition(-scores, n)[:n]
    return idx[np.argsort(-scores[idx])]
