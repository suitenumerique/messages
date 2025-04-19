"""
Unit tests for the filter_root_paths utility function.
"""

from django_ltree.fields import PathValue

from core.api.utils import filter_root_paths


def test_api_utils_filter_root_paths_success():
    """
    The `filter_root_paths` function should correctly identify root paths
    from a given list of paths.

    This test uses a list of paths with missing intermediate paths to ensure that
    only the minimal set of root paths is returned.
    """
    paths = [
        PathValue("0001"),
        PathValue("0001.0001"),
        PathValue("0001.0001.0001"),
        PathValue("0001.0001.0002"),
        # missing 00010002
        PathValue("0001.0002.0001"),
        PathValue("0001.0002.0002"),
        PathValue("0002"),
        PathValue("0002.0001"),
        PathValue("0002.0002"),
        # missing 0003
        PathValue("0003.0001"),
        PathValue("0003.0001.0001"),
        PathValue("0003.0002"),
        # missing 0004
        # missing 00040001
        # missing 000400010001
        # missing 000400010002
        PathValue("0004.0001.0003"),
        PathValue("0004.0001.0003.0001"),
        PathValue("0004.0001.0004"),
    ]
    filtered_paths = filter_root_paths(paths, skip_sorting=True)
    assert filtered_paths == [
        PathValue("0001"),
        PathValue("0002"),
        PathValue("0003.0001"),
        PathValue("0003.0002"),
        PathValue("0004.0001.0003"),
        PathValue("0004.0001.0004"),
    ]


def test_api_utils_filter_root_paths_sorting():
    """
    The `filter_root_paths` function should fail is sorting is skipped and paths are not sorted.

    This test verifies that when sorting is skipped, the function respects the input order, and
    when sorting is enabled, the result is correctly ordered and minimal.
    """
    paths = [
        PathValue("0001"),
        PathValue("0001.0001"),
        PathValue("0001.0001.0001"),
        PathValue("0001.0002.0002"),
        PathValue("0001.0001.0002"),
        PathValue("0001.0002.0001"),
        PathValue("0002.0001"),
        PathValue("0002"),
        PathValue("0002.0002"),
        PathValue("0003.0001.0001"),
        PathValue("0003.0001"),
        PathValue("0003.0002"),
        PathValue("0004.0001.0003.0001"),
        PathValue("0004.0001.0003"),
        PathValue("0004.0001.0004"),
    ]
    filtered_paths = filter_root_paths(paths, skip_sorting=True)
    assert filtered_paths == [
        PathValue("0001"),
        PathValue("0002.0001"),
        PathValue("0002"),
        PathValue("0003.0001.0001"),
        PathValue("0003.0001"),
        PathValue("0003.0002"),
        PathValue("0004.0001.0003.0001"),
        PathValue("0004.0001.0003"),
        PathValue("0004.0001.0004"),
    ]
    filtered_paths = filter_root_paths(paths)
    assert filtered_paths == [
        PathValue("0001"),
        PathValue("0002"),
        PathValue("0003.0001"),
        PathValue("0003.0002"),
        PathValue("0004.0001.0003"),
        PathValue("0004.0001.0004"),
    ]
