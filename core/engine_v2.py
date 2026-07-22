"""Public Lumi DM source-runtime engine facade.

Wave 2 supplies secure capture and organisation. Wave 3 adds media, torrent,
archive and post-processing engines. Explicit re-exports preserve the Wave 2
category-aware HTTP and encrypted Repair Link boundaries after Wave 3 imports the
shared runtime surface.
"""
from .v2.runtime_wave2 import *  # noqa: F401,F403
from .v2 import runtime_guard as _runtime_guard  # noqa: F401
from .v2.wave2 import *  # noqa: F401,F403
from .v2.wave2_repair import *  # noqa: F401,F403
from .v3.runtime_wave3 import *  # noqa: F401,F403

# Wave 3 intentionally replaces video/torrent functions, but its shared runtime
# exports must never replace the category-aware HTTP or encrypted repair paths.
from .v2.wave2 import start_http as start_http  # noqa: F401,E402
from .v2.wave2_repair import (  # noqa: F401,E402
    repair_download_link as repair_download_link,
    repair_from_capture as repair_from_capture,
)
