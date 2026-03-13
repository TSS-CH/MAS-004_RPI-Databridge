from __future__ import annotations

from pathlib import Path
import sys


def _ensure_repo_on_path():
    candidates = []
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    cwd = Path.cwd().resolve()
    candidates.append(cwd)
    candidates.extend(cwd.parents)

    seen = set()
    for base in candidates:
        sibling_repo = base / "MAS-004_VJ6530-ZBC-Bridge"
        package_dir = sibling_repo / "mas004_vj6530_zbc_bridge"
        sibling_repo_str = str(sibling_repo)
        if package_dir.exists() and sibling_repo_str not in seen:
            seen.add(sibling_repo_str)
            if sibling_repo_str not in sys.path:
                sys.path.insert(0, sibling_repo_str)


_ensure_repo_on_path()
from mas004_vj6530_zbc_bridge import ZbcBridgeClient  # type: ignore[attr-defined]
from mas004_vj6530_zbc_bridge._zbc_library import AsyncSubscriptionId, MessageId, ZbcClient  # type: ignore[attr-defined]


__all__ = ["AsyncSubscriptionId", "MessageId", "ZbcBridgeClient", "ZbcClient"]
