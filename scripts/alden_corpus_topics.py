"""Compatibility entry point for retired automatic Raw topic indexing.

Raw text is retrieval evidence; it does not establish confirmed graph topics.
Existing graph callers may still invoke ``index`` while being migrated.
"""


def index(graph, source, *, chat: str = '', minimum: int = 20) -> dict:
    """Return legacy counters without inspecting or modifying either store.

    The arguments remain for existing callers. No topic derivation, promotion,
    graph cleanup, metadata write, or transaction commit happens here.
    """
    return {'topics': 0, 'written': 0, 'with_samples': 0}
