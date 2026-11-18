"""Use the backend's own wording matcher, so the AI explains exactly the old and new text the UI shows.

The backend route GET /api/v1/acts/{id}/changes?include_wording=true fills old_text / new_text in two ways:
  1. a reviewed comparison in act_wording_comparisons ("REVIEWED_MAPPING"), else
  2. app/services/eurlex_wording.py: match_stored() on the stored versions and the amendment quotes.
This module loads that file read-only from the backend folder and runs match_stored() on the same database rows.
The backend is not changed and no other backend module is imported (its two imports are replaced by stand-ins,
because match_stored() itself does not use them).

    wording = BackendWording.load(r"C:\\...\\legal_radar_backend")
    index = wording.index(versions, elements, external_id)       # rows as dicts, like the backend's models
    result = wording.match(change_row, changing_index, changed_index)
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import types

RELATIVE = Path("app") / "services" / "eurlex_wording.py"


class BackendWording:
    def __init__(self, module, path: Path):
        self.module, self.path = module, path
        self.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def load(cls, backend_dir) -> "BackendWording":
        path = Path(backend_dir) / RELATIVE
        if not path.is_file():
            raise FileNotFoundError(f"{RELATIVE} not found in {backend_dir}. Give the backend folder with --backend-dir.")
        stand_ins = {}
        if importlib.util.find_spec("sqlalchemy") is None:
            stand_ins["sqlalchemy"] = types.ModuleType("sqlalchemy")
            stand_ins["sqlalchemy"].select = lambda *args, **kwargs: None
        models = types.ModuleType("app.models")
        for name in ("InstrumentVersion", "LegalElement", "LegalInstrument", "SourceDocument"):
            setattr(models, name, type(name, (), {}))
        stand_ins["app.models"] = models
        if "app" not in sys.modules:
            stand_ins["app"] = types.ModuleType("app")
        saved = {name: sys.modules.get(name) for name in stand_ins}
        sys.modules.update(stand_ins)
        try:
            spec = importlib.util.spec_from_file_location("lr_backend_eurlex_wording", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            for name, old in saved.items():      # leave no stand-in behind
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old
        if not hasattr(module, "match_stored") or not hasattr(module, "DocumentIndex"):
            raise ImportError(f"{path} has no match_stored/DocumentIndex: backend version not supported")
        return cls(module, path)

    def index(self, versions: list[dict], elements: list[dict], identity: str | None):
        """The backend's DocumentIndex over one document's rows (EN versions only, as in the backend)."""
        return self.module.DocumentIndex(versions, elements, identity=identity)

    def match(self, change: dict, changing_index, changed_index) -> dict:
        """old_text, new_text, comparison_status, comparison_provenance - as the UI gets them."""
        return self.module.match_stored(change, changing_index, changed_index)
