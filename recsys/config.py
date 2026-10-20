"""Paths and constants shared by training and the web app."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ARTIFACTS_DIR = ROOT / "artifacts"

RANDOM_STATE = 42
RELEVANCE_THRESHOLD = 4.0  # a rating >= 4 counts as "the user liked it"
TOP_K = 10

# Curated genres shown in the UI. Each genre is a set of Goodreads tags.
GENRES = {
    "Fantasy": ["fantasy", "urban-fantasy", "magic", "epic-fantasy", "high-fantasy"],
    "Science Fiction": ["science-fiction", "sci-fi", "scifi", "space-opera"],
    "Romance": ["romance", "contemporary-romance", "historical-romance", "chick-lit"],
    "Mystery & Crime": ["mystery", "crime", "mysteries", "detective"],
    "Thriller": ["thriller", "suspense", "mystery-thriller", "thrillers"],
    "Horror": ["horror"],
    "Young Adult": ["young-adult", "ya", "teen", "ya-fiction"],
    "Classics": ["classics", "classic", "1001-books"],
    "Historical Fiction": ["historical-fiction", "historical"],
    "Non-Fiction": ["non-fiction", "nonfiction"],
    "Biography & Memoir": ["biography", "memoir", "autobiography", "memoirs"],
    "History": ["history"],
    "Science": ["science", "popular-science"],
    "Philosophy": ["philosophy"],
    "Self-Help & Psychology": ["self-help", "psychology", "personal-development"],
    "Business": ["business", "economics"],
    "Children's": ["childrens", "children", "children-s", "picture-books", "kids", "middle-grade"],
    "Comics & Manga": ["graphic-novels", "comics", "manga", "graphic-novel"],
    "Poetry": ["poetry"],
    "Dystopian": ["dystopian", "dystopia"],
    "Paranormal": ["paranormal", "vampires", "supernatural", "paranormal-romance"],
    "Humor": ["humor", "humour", "funny", "comedy"],
}

# Tags that describe a reader's shelf rather than the book itself.
NOISE_TAG = re.compile(
    r"""^(
        -.*|.*to-?read.*|.*currently.*|.*favou?rite.*|fav.*|own|owned.*|.*-owned|own-.*|.*-own|.*shelf.*|
        .*kindle.*|.*nook.*|.*e-?books?.*|.*audio.*|.*audible.*|.*library.*|.*borrowed.*|
        .*wish.*|.*to-?buy.*|.*re-?read.*|.*dnf.*|.*did-not-finish.*|abandoned|unfinished|
        default|books?|novels?|series|my-books|book-club|english|school|maybe|tbr|
        .*\d-?stars?.*|.*read-?in.*|read|.*20\d\d.*|have-.*|.*-have|.*bought.*|
        .*gave-up.*|read-for-.*|for-school|.*on-hold.*|.*finished.*|.*to-be-read.*|.*must-read.*|.*recommended.*
    )$""",
    re.VERBOSE,
)
