"""Content features: a TF-IDF weighted book x (tag + author) matrix."""
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.preprocessing import normalize


def build_content_matrix(books, book_tags, min_books_per_tag=5, author_weight=0.6):
    """Rows are L2-normalised, so a dot product between rows is cosine similarity.

    Tags: log(1 + count) as term frequency, then IDF so that broad tags like
    "fiction" matter less than specific ones like "space-opera".
    Authors: one-hot on each listed author, so books by the same writer are close.
    """
    n_books = len(books)
    clean = book_tags[~book_tags["noise"]]
    tag_freq = clean.groupby("tag_name")["book_id"].nunique()
    vocab = tag_freq[tag_freq >= min_books_per_tag].index
    clean = clean[clean["tag_name"].isin(vocab)]
    tag_index = {t: i for i, t in enumerate(sorted(vocab))}

    rows = clean["book_id"].to_numpy() - 1
    cols = clean["tag_name"].map(tag_index).to_numpy()
    vals = np.log1p(clean["count"].to_numpy().astype(float))
    tags_m = sparse.csr_matrix((vals, (rows, cols)), shape=(n_books, len(tag_index)))
    tags_m = normalize(TfidfTransformer(sublinear_tf=False).fit_transform(tags_m))

    authors = (
        books[["book_id", "authors"]]
        .assign(author=lambda d: d["authors"].str.split(","))
        .explode("author")
        .assign(author=lambda d: d["author"].str.strip())
    )
    author_codes, author_names = pd.factorize(authors["author"])
    author_m = sparse.csr_matrix(
        (np.ones(len(authors)), (authors["book_id"].to_numpy() - 1, author_codes)),
        shape=(n_books, len(author_names)),
    )
    author_m = normalize(author_m) * author_weight

    content = normalize(sparse.hstack([tags_m, author_m]).tocsr())
    feature_names = list(sorted(vocab)) + ["author:" + a for a in author_names]
    return content.astype(np.float32), feature_names
