from __future__ import annotations

import argparse
import re
from pathlib import Path


MARKER = "MASTER_EDIT_ENGINE_HOOK_V7"
OLD_MARKERS = (
    "MASTER_EDIT_ENGINE_HOOK_V1",
    "MASTER_EDIT_ENGINE_HOOK_V2",
    "MASTER_EDIT_ENGINE_HOOK_V3",
    "MASTER_EDIT_ENGINE_HOOK_V4",
    "MASTER_EDIT_ENGINE_HOOK_V5",
    "MASTER_EDIT_ENGINE_HOOK_V6",
    "MASTER_EDIT_ENGINE_HOOK_V7",
)
IMPORT_LINE = "from master_engine.telegram_bridge import install_master_engine as _install_master_engine"
CALL_LINE = "    _install_master_engine(app, sys.modules[__name__])"


def remove_text(value: str) -> str:
    result = value
    for marker in OLD_MARKERS:
        result = result.replace(f"# {marker}\n{IMPORT_LINE}\n\n", "", 1)
        result = result.replace(f"\n    # {marker}\n{CALL_LINE}", "", 1)
    # Recover a markerless early draft without touching unrelated imports/calls.
    if result.count(IMPORT_LINE) == 1 and result.count(CALL_LINE) == 1:
        result = result.replace(IMPORT_LINE + "\n\n", "", 1)
        result = result.replace("\n" + CALL_LINE, "", 1)
    return result


def apply_text(old: str) -> str:
    baseline = remove_text(old)
    s = baseline
    anchor = re.search(r"(?m)^def\s+main\s*\(\s*\)\s*(?:->\s*None\s*)?:\s*$", s)
    if not anchor:
        raise RuntimeError("def main() anchor not found; bot.py untouched")
    s = s[:anchor.start()] + f"# {MARKER}\n{IMPORT_LINE}\n\n" + s[anchor.start():]
    main_match = re.search(r"(?m)^def\s+main\s*\(\s*\)\s*(?:->\s*None\s*)?:\s*$", s)
    if not main_match:
        raise RuntimeError("def main() vanished; bot.py untouched")
    next_def = re.search(r"(?m)^def\s+[A-Za-z_]\w*\s*\(", s[main_match.end():])
    main_end = main_match.end() + next_def.start() if next_def else len(s)
    main_block = s[main_match.start():main_end]
    app_anchor = re.search(r"(?m)^(?P<i>[ \t]+)app\s*=\s*build_application\(\)\s*$", main_block)
    if not app_anchor:
        raise RuntimeError("app = build_application() anchor not found; bot.py untouched")
    absolute_end = main_match.start() + app_anchor.end()
    s = s[:absolute_end] + f"\n    # {MARKER}\n{CALL_LINE}" + s[absolute_end:]
    if s.count(IMPORT_LINE) != 1 or s.count(CALL_LINE) != 1:
        raise RuntimeError("Master hook duplicate/invalid; bot.py untouched")
    if remove_text(s) != baseline:
        raise RuntimeError("Isolation audit failed; bot.py untouched")
    return s


def apply(path: Path) -> None:
    old = path.read_text(encoding="utf-8")
    new = apply_text(old)
    if new == old:
        print("✅ Master Editor V7 hook already installed")
        return
    path.write_text(new, encoding="utf-8")
    print("✅ Exactly two reversible V7 hook lines installed")
    print("✅ Every legacy editor function body preserved")


def remove(path: Path) -> None:
    old = path.read_text(encoding="utf-8")
    path.write_text(remove_text(old), encoding="utf-8")
    print("✅ Master hook removed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "remove"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        (apply if args.action == "apply" else remove)(args.path)
    except Exception as exc:
        print("❌ " + str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
