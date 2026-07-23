from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_os_routes_register_from_fresh_source(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "data")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import server; "
                "routes={rule.rule for rule in server.app.url_map.iter_rules()}; "
                "required={'/api/v5/os/catalogue','/api/v5/os/search',"
                "'/api/v5/os/windows/resolve','/api/v5/os/stage'}; "
                "assert required <= routes, required-routes"
            ),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
