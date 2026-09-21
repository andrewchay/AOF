"""K04：_SnapshotConnector cursor 覆盖四维输入的确定性验证。"""

from __future__ import annotations

from bridge.knowledge_build_mcp import _SnapshotConnector


def _docs() -> list[dict]:
    return [
        {"relative_path": "notes/alpha.md", "title": "Alpha 概念", "sha256": "aaa", "resolved_links": ["Beta 概念"]},
        {"relative_path": "notes/beta.md", "title": "Beta 概念", "sha256": "bbb", "resolved_links": []},
    ]


def test_snapshot_cursor_stable_for_identical_input() -> None:
    first = _SnapshotConnector(_docs()).fetch(None, None)  # type: ignore[arg-type]
    second = _SnapshotConnector(_docs()).fetch(None, None)  # type: ignore[arg-type]
    assert first.cursor_to == second.cursor_to


def test_snapshot_cursor_changes_on_any_dimension() -> None:
    baseline = _SnapshotConnector(_docs()).fetch(None, None).cursor_to  # type: ignore[arg-type]

    # 改 sha256（正文）
    changed_hash = [dict(_docs()[0], sha256="ccc"), _docs()[1]]
    assert _SnapshotConnector(changed_hash).fetch(None, None).cursor_to != baseline  # type: ignore[arg-type]

    # 改标题
    changed_title = [dict(_docs()[0], title="Alpha2"), _docs()[1]]
    assert _SnapshotConnector(changed_title).fetch(None, None).cursor_to != baseline  # type: ignore[arg-type]

    # 改链接
    changed_links = [dict(_docs()[0], resolved_links=[]), _docs()[1]]
    assert _SnapshotConnector(changed_links).fetch(None, None).cursor_to != baseline  # type: ignore[arg-type]

    # 改路径
    changed_path = [dict(_docs()[0], relative_path="other/alpha.md"), _docs()[1]]
    assert _SnapshotConnector(changed_path).fetch(None, None).cursor_to != baseline  # type: ignore[arg-type]
