from __future__ import annotations

from pathlib import Path
import sys


def _ensure_repo_on_path():
    repo_root = Path(__file__).resolve().parents[1]
    sibling_repo = repo_root.parent / "MAS-004_VJ6530-ZBC-Bridge"
    package_dir = sibling_repo / "mas004_vj6530_zbc_bridge"
    if package_dir.exists():
        sibling_repo_str = str(sibling_repo)
        if sibling_repo_str not in sys.path:
            sys.path.insert(0, sibling_repo_str)


try:
    from mas004_vj6530_zbc_bridge import ZbcBridgeClient  # type: ignore[attr-defined]
except ImportError:
    _ensure_repo_on_path()
    from mas004_vj6530_zbc_bridge import ZbcBridgeClient  # type: ignore[attr-defined]


__all__ = ["ZbcBridgeClient"]
