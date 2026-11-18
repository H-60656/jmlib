"""P4 new-download pipeline: jmcomic download -> jmlib ingest.

``jmlib download <ids...>`` NEVER edits the user's option.yml.  It reads the
base config into memory and overlays only two keys:

  * ``dir_rule`` -> a staging tree ``Bd/{Aid}/{Pindex}`` (NO ``.zip`` in the
    directory DSL, or images would land inside a folder literally named
    ``1.zip``);
  * ``plugins`` -> the single ``jmlib_ingest`` after_album plugin (replaces
    ``download_cover`` and any other plugin groups).

YAML comments — including any credential-bearing commented block — are dropped
by the parser and are never logged, written to disk, or submitted anywhere.
Only the in-memory dict is passed to ``JmOption.construct``.

Both the installed jmcomic (2.7.6) and the upstream checkout (2.7.11, when it
is on PYTHONPATH) are supported: only APIs shared by both are used.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from jmlib import paths

INGEST_PLUGIN_KEY = "jmlib_ingest"


def _parse(text: str) -> dict:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - jmcomic depends on PyYAML
        raise RuntimeError("PyYAML is required to read option.yml") from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("option.yml did not parse to a mapping")
    return copy.deepcopy(data)


def _read_base(option: Path | None) -> tuple[dict, str]:
    """Return (base dict, redacted description).  Never returns credential values."""
    if option is not None:
        p = Path(option)
        if not p.is_file():
            raise FileNotFoundError(f"option file not found: {p}")
        return _parse(p.read_text(encoding="utf-8")), f"option file {p.name}"

    env = os.environ.get("JM_OPTION_PATH")
    if env and Path(env).is_file():
        return _parse(Path(env).read_text(encoding="utf-8")), "JM_OPTION_PATH"

    import jmcomic

    return copy.deepcopy(jmcomic.JmModuleConfig.option_default_dict()), "jmcomic defaults"


def build_option_dict(option: Path | None = None) -> dict:
    """Pure overlay, offline-testable.  Does not import jmcomic for the overlay itself."""
    dic, _ = _read_base(option)
    dic.pop("filepath", None)
    dic.pop("version", None)
    dic["dir_rule"] = {
        "base_dir": str(paths.STAGING),
        # Directory DSL only.  The .zip suffix belongs to the packer, not here.
        "rule": "Bd/{Aid}/{Pindex}",
        "normalize_zh": None,
    }
    dic["plugins"] = {
        # Let plugin failures surface instead of being swallowed into a log.
        "valid": "raise",
        "after_album": [{"plugin": INGEST_PLUGIN_KEY, "safe": False}],
    }
    return dic


def _register_and_construct(dic: dict):
    import jmcomic

    from jmlib import jm_plugin

    # jmcomic validates plugin keys during option construction, so registration
    # must happen first (see assets/docs/sources/tutorial/6_plugin.md).
    jmcomic.JmModuleConfig.register_plugin(jm_plugin.JmlibIngestPlugin)
    return jmcomic.JmModuleConfig.option_class().construct(copy.deepcopy(dic))


def run(ids: list[str], option: Path | None = None) -> int:
    if not ids:
        print("download: pass at least one album id (or p-prefixed photo id)")
        return 2

    try:
        opt = _register_and_construct(build_option_dict(option))
    except Exception as exc:
        print(f"download: cannot build jmcomic option: {type(exc).__name__}: {exc}")
        return 2

    import jmcomic

    failures = 0
    done = 0
    for raw in ids:
        token = str(raw).strip()
        if not token:
            continue
        try:
            if token.lower().startswith("p") and token[1:].isdigit():
                jmcomic.download_photo(token[1:], option=opt)
            elif token.isdigit():
                jmcomic.download_album(token, option=opt)
            else:
                raise ValueError(f"invalid id {token!r}; use digits or p<digits>")
            done += 1
            print(f"download: {token} done (ingested directly into the catalog)")
        except Exception as exc:
            failures += 1
            print(f"download: {token} FAILED: {type(exc).__name__}: {exc}")

    print(f"download: {done} succeeded, {failures} failed")
    if failures:
        print("download: run `jmlib index` to reconcile any partially written chapters")
    return 0 if failures == 0 else 1
