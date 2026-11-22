"""Board-level helpers: small pure functions the UI can show and tests can check."""

import pandas as pd


def source_health(df, expected_books, errors=None):
    """Per-book health for the loaded board.

    Returns one row per expected book:
        {"book", "lines", "status", "detail"}
    status is one of:
        "ok"      -> returned lines
        "empty"   -> enabled but returned nothing (silent failure or off-season)
        "error"   -> the source reported an error (from `errors`)
    `errors` is the {source: message} map from load_props; a key is matched to a
    book when the book label appears in it (e.g. "PrizePicks/NFL" or
    "SportsGameOdds"). This makes the "nothing showing up" case legible: the user
    sees which source gave what, instead of a blank board.
    """
    errors = errors or {}
    counts = {}
    if df is not None and len(df) and "book" in df:
        counts = df["book"].value_counts().to_dict()

    # errors that mention a given book label
    def book_errors(book):
        return [m for k, m in errors.items() if book.lower() in str(k).lower()]

    out = []
    for book in expected_books:
        n = int(counts.get(book, 0))
        errs = book_errors(book)
        if n > 0:
            status, detail = "ok", f"{n} lines"
        elif errs:
            status, detail = "error", errs[0]
        else:
            status, detail = "empty", "no lines (off-season or blocked)"
        out.append({"book": book, "lines": n, "status": status, "detail": detail})
    return out


def enabled_books(use_pp, use_ud, use_sgo):
    """The book labels we expect data for, given the source toggles.

    PrizePicks + Underdog come from the scrapers; SGO also serves both, so turning
    SGO on doesn't add new book labels — it's an alternate source for the same two.
    """
    books = []
    if use_pp or use_sgo:
        books.append("PrizePicks")
    if use_ud or use_sgo:
        books.append("Underdog")
    return books
