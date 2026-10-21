"""Paths and constants shared by training and the web app."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ARTIFACTS_DIR = ROOT / "artifacts"

RANDOM_STATE = 42
RELEVANCE_THRESHOLD = 4.0  # a rating >= 4 counts as "the user liked it"
TOP_K = 10

# Curated genres shown in the UI. Each genre matches Goodreads tags by keyword,
# so compound tags count too ("horror-thriller" counts for Horror and Thriller).
GENRES = {
    "Fantasy": r"fantasy|magic",
    "Science Fiction": r"sci-?fi|scifi|science-fiction|sf|space-opera",
    "Romance": r"romance|romantic|chick-?lit",
    "Mystery & Crime": r"mystery|mysteries|crime|detective|murder-mystery|whodunit",
    "Thriller": r"thrillers?|suspense",
    "Horror": r"horror|scary",
    "Young Adult": r"young-adult|ya|teen",
    "Classics": r"classics?|1001-books|1001",
    "Historical Fiction": r"historical-fiction|historical|historical-romance",
    "Non-Fiction": r"non-?fiction",
    "Biography & Memoir": r"biography|biographies|memoirs?|autobiography",
    "History": r"history|american-history|world-history|military-history",
    "Science": r"science|popular-science",
    "Philosophy": r"philosophy",
    "Self-Help & Psychology": r"self-help|psychology|personal-development|self-improvement",
    "Business": r"business|economics|entrepreneurship",
    "Children's": r"childrens|children|children-s|picture-books?|kids|middle-grade|juvenile",
    "Comics & Manga": r"graphic-novels?|comics|comic-books?|manga",
    "Poetry": r"poetry|poems",
    "Dystopian": r"dystopian|dystopia|post-apocalyptic",
    "Paranormal": r"paranormal|vampires?|supernatural|werewolves|shapeshifters|ghosts",
    "Humor": r"humou?r|funny|comedy",
}

# Genres that describe a book's form or audience rather than its content. They
# are judged separately, so a huge "classics" count can't hide "romance".
FORM_GENRES = {"Classics", "Young Adult", "Children's", "Non-Fiction", "Comics & Manga", "Poetry"}

# A book tagged "non-fiction" by more readers than "fiction" can't be in a
# fiction-only genre, and the other way round.
FICTION_ONLY = {"Fantasy", "Science Fiction", "Romance", "Mystery & Crime", "Thriller", "Horror",
                "Historical Fiction", "Dystopian", "Paranormal"}
NONFICTION_ONLY = {"Non-Fiction", "Biography & Memoir", "History", "Science", "Self-Help & Psychology", "Business"}

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
