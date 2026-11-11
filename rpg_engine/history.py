"""Safe FTS5 queries adapted from the source engine's history index."""

import re

TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def fts_expression(query: str, mode: str) -> str:
    # Quote every token, never pass arbitrary user FTS expressions to SQLite.
    terms = TOKEN.findall(query)
    if not terms:
        raise ValueError('Search query has no searchable words')
    if len(terms) > 12:
        raise ValueError('Use a narrower query (maximum 12 terms)')
    op = ' OR ' if mode == 'any' else ' AND '
    return op.join('"' + word.replace('"', '') + '"' for word in terms)
