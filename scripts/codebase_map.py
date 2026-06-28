#!/usr/bin/env python3
"""
codebase_map.py — Gio's codebase-map generator.

Builds a labeled, regenerable index of a repository so an agent can jump
straight to the right file and line range instead of re-discovering the
structure every task. The map lists, per file: a one-line purpose and the key
symbols (classes / functions / methods ...) with their line ranges.

It is meant to be used "map-then-verify": consult the map to locate code, then
Read only that span to confirm it still matches before acting. The map carries
per-file mtime + a short content hash so staleness can be detected (`--check`)
and the map regenerated when the code drifts. Never trust the map blindly.

Symbol extraction is TIERED and degrades gracefully — no third-party package is
required, but high-performance ones are used automatically when present:

    tree-sitter (tree_sitter_language_pack)  -> best, multi-language   [optional]
    universal-ctags (ctags on PATH)          -> broad, multi-language   [optional]
    stdlib `ast`                             -> exact, Python only      [always]
    regex heuristic                          -> last resort, any language[always]

Nothing is sent anywhere. This only reads local files.

Usage:
    python3 codebase_map.py                  # generate map for the current repo
    python3 codebase_map.py --root path/     # map a specific directory
    python3 codebase_map.py --check          # report staleness vs existing map
    python3 codebase_map.py --json           # machine-readable map to stdout
    python3 codebase_map.py --engine ast     # force an engine (default: auto)
    python3 codebase_map.py --help
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# CONSTANTS — edit these to taste. Notes inline.
# ---------------------------------------------------------------------------

# Output filenames written at the repo root.
MD_FILENAME = "CODEBASE_MAP.md"
JSON_FILENAME = ".codebase-map.json"

# Directories never worth indexing (vendored, generated, or VCS internals).
DEFAULT_IGNORES = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", "target", "out", "coverage",
    ".idea", ".vscode", "vendor", ".tox", ".gradle", "bin", "obj",
}

# Skip files bigger than this (bytes). Big files are usually data/minified,
# not source worth mapping — and reading them would defeat the point.
MAX_FILE_BYTES = 500_000

# Cap symbols listed per file in the Markdown view so one giant file can't
# dominate the map. The JSON keeps everything; only the human view is trimmed.
DEFAULT_MAX_SYMBOLS_PER_FILE = 40

# Source file extensions we know how to label, mapped to a language name.
LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".java": "java", ".kt": "kotlin", ".swift": "swift", ".scala": "scala",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".m": "objc", ".sh": "shell",
    ".bash": "shell", ".lua": "lua", ".r": "r", ".jl": "julia",
    ".ex": "elixir", ".exs": "elixir", ".dart": "dart", ".vue": "vue",
    ".svelte": "svelte", ".sql": "sql",
    ".md": "markdown", ".markdown": "markdown",
}

# Language-agnostic definition patterns for the regex fallback. Each entry is
# (compiled-regex, kind); the first capture group is the symbol name. Kept
# deliberately conservative — better to miss a symbol than mislabel a line.
REGEX_PATTERNS = [
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:public|private|protected|static|\s)*\bclass\s+([A-Za-z_]\w*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), "function"),  # rust
    (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("), "function"),               # go
    (re.compile(r"^\s*def\s+([A-Za-z_]\w*)"), "function"),                                       # py/ruby
    (re.compile(r"^\s*(?:export\s+)?(?:type|interface|struct|enum)\s+([A-Za-z_]\w*)"), "type"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"), "function"),  # JS arrow fn
]

# tree-sitter node types treated as "definitions" across languages (generic
# extraction — no per-language query files needed).
TS_DEF_TYPES = {
    "function_definition": "function", "function_declaration": "function",
    "method_definition": "method", "method_declaration": "method",
    "class_definition": "class", "class_declaration": "class",
    "interface_declaration": "type", "type_alias_declaration": "type",
    "struct_item": "type", "enum_item": "type", "trait_item": "type",
    "impl_item": "impl", "function_item": "function", "mod_item": "module",
    "type_declaration": "type", "type_spec": "type",
}

# ---------------------------------------------------------------------------


def lang_for(path: str):
    return LANG_BY_EXT.get(Path(path).suffix.lower())


def short_sha(data: bytes):
    return hashlib.sha256(data).hexdigest()[:12]


def git_commit(root: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def discover_files(root: Path):
    """Return source files relative to root, honoring .gitignore when possible."""
    rels = []
    try:
        # Tracked + untracked files, but honoring .gitignore (so new files are
        # mapped before they're committed, and ignored files stay out).
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files",
             "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            rels = out.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        rels = []

    if not rels:  # not a git repo (or git missing) — walk and prune.
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORES]
            for fn in filenames:
                full = Path(dirpath) / fn
                rels.append(str(full.relative_to(root)))

    out_files = []
    for rel in rels:
        if any(part in DEFAULT_IGNORES for part in Path(rel).parts):
            continue
        if Path(rel).name in (MD_FILENAME, JSON_FILENAME):
            continue  # never map the map's own output
        if lang_for(rel) is None:
            continue
        full = root / rel
        try:
            if not full.is_file() or full.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out_files.append(rel)
    return sorted(out_files)


# --- symbol extractors (one per engine) -----------------------------------

def extract_ast(text: str):
    """Python via stdlib ast: classes/functions/methods + exact line ranges."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None  # signal caller to fall back
    syms = []

    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(child, ast.ClassDef) else (
                    "method" if prefix else "function")
                name = prefix + child.name
                syms.append({
                    "name": name, "kind": kind,
                    "line_start": child.lineno,
                    "line_end": getattr(child, "end_lineno", child.lineno),
                })
                if isinstance(child, ast.ClassDef):
                    visit(child, prefix=child.name + ".")

    visit(tree)

    # Module-level UPPER_CASE constants — high-value navigation targets that
    # `--find` should be able to resolve (e.g. config/pricing constants).
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for t in targets:
            if t.id.isupper():
                syms.append({
                    "name": t.id, "kind": "constant",
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                })
    return syms


def file_purpose_python(text: str):
    """First line of the module docstring, if any."""
    try:
        tree = ast.parse(text)
        doc = ast.get_docstring(tree)
        if doc:
            return doc.strip().splitlines()[0][:120]
    except (SyntaxError, ValueError):
        pass
    return None


def extract_regex(text: str):
    """Language-agnostic heuristic fallback."""
    syms = []
    for i, line in enumerate(text.splitlines(), start=1):
        for pat, kind in REGEX_PATTERNS:
            m = pat.match(line)
            if m:
                syms.append({"name": m.group(1), "kind": kind,
                             "line_start": i, "line_end": i})
                break
    return syms


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")


def extract_markdown(text: str):
    """ATX headings as navigable symbols (skips headings inside code fences)."""
    syms = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        if _MD_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _MD_HEADING.match(line)
        if m:
            level = len(m.group(1))
            syms.append({"name": m.group(2).strip(), "kind": f"h{level}",
                         "line_start": i, "line_end": i})
    return syms


def markdown_purpose(text: str):
    """First H1 (or first heading) as the file's one-line purpose."""
    for s in extract_markdown(text):
        if s["kind"] == "h1":
            return s["name"][:120]
    return None


def extract_ctags(root: Path, files):
    """Multi-language via universal-ctags JSON, if ctags is on PATH."""
    if not shutil.which("ctags"):
        return None
    try:
        proc = subprocess.run(
            ["ctags", "--output-format=json", "--fields=+ne", "-f", "-",
             *[str(root / f) for f in files]],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode not in (0, 1):  # ctags returns 1 on some warnings
        return None
    by_file = {}
    for line in proc.stdout.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("_type") != "tag" or "name" not in rec or "path" not in rec:
            continue
        try:
            rel = str(Path(rec["path"]).resolve().relative_to(root.resolve()))
        except ValueError:
            rel = rec["path"]
        start = rec.get("line")
        if start is None:
            continue
        by_file.setdefault(rel, []).append({
            "name": rec["name"], "kind": rec.get("kind", "symbol"),
            "line_start": int(start),
            "line_end": int(rec.get("end", start)),
        })
    return by_file


_TS_CACHE = {}


def _ts_parser(language: str):
    if language in _TS_CACHE:
        return _TS_CACHE[language]
    try:
        from tree_sitter_language_pack import get_parser
        parser = get_parser(language)
    except Exception:
        parser = None
    _TS_CACHE[language] = parser
    return parser


def extract_treesitter(text: str, language: str):
    """Generic multi-language extraction via tree-sitter, if installed."""
    parser = _ts_parser(language)
    if parser is None:
        return None
    try:
        tree = parser.parse(text.encode("utf-8", errors="ignore"))
    except Exception:
        return None
    syms = []

    def name_of(node):
        for child in node.children:
            if child.type in ("identifier", "name", "type_identifier",
                              "field_identifier", "constant"):
                return child.text.decode("utf-8", errors="ignore")
        return None

    def walk(node):
        kind = TS_DEF_TYPES.get(node.type)
        if kind:
            nm = name_of(node)
            if nm:
                syms.append({
                    "name": nm, "kind": kind,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                })
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return syms


# --- map assembly ----------------------------------------------------------

def detect_engine(requested: str):
    """Resolve 'auto' to the best available engine name (for reporting)."""
    if requested != "auto":
        return requested
    try:
        import tree_sitter_language_pack  # noqa: F401
        return "treesitter"
    except Exception:
        pass
    if shutil.which("ctags"):
        return "ctags"
    return "ast+regex"


def symbols_for_file(rel, text, language, engine, ctags_index):
    """Pick the best symbols for one file given the resolved engine."""
    if language == "markdown":
        return extract_markdown(text), "markdown"
    if engine == "treesitter":
        ts = extract_treesitter(text, language)
        if ts is not None:
            return ts, "treesitter"
    if engine in ("treesitter", "ctags") and ctags_index is not None:
        if rel in ctags_index:
            return ctags_index[rel], "ctags"
    if language == "python":
        a = extract_ast(text)
        if a is not None:
            return a, "ast"
    return extract_regex(text), "regex"


def build_map(root: Path, requested_engine: str, max_symbols: int):
    files = discover_files(root)
    engine = detect_engine(requested_engine)

    # Run ctags once for the whole repo when it's the resolved/forced engine.
    ctags_index = None
    if engine in ("ctags",) or (engine == "treesitter"):
        ctags_index = extract_ctags(root, files)
    if requested_engine == "ctags" and ctags_index is None:
        engine = "ast+regex"  # forced ctags but unavailable — be honest

    file_entries = []
    for rel in files:
        full = root / rel
        try:
            raw = full.read_bytes()
        except OSError:
            continue
        text = raw.decode("utf-8", errors="ignore")
        language = lang_for(rel)
        syms, used = symbols_for_file(rel, text, language, engine, ctags_index)
        syms.sort(key=lambda s: s["line_start"])
        if language == "python":
            purpose = file_purpose_python(text)
        elif language == "markdown":
            purpose = markdown_purpose(text)
        else:
            purpose = None
        try:
            mtime = int(full.stat().st_mtime)
        except OSError:
            mtime = 0
        file_entries.append({
            "path": rel, "lang": language, "mtime": mtime,
            "sha": short_sha(raw), "size": len(raw), "purpose": purpose,
            "engine": used, "symbols": syms,
        })

    return {
        "schema": 1,
        "generated_at": int(time.time()),
        "root": str(root),
        "git_commit": git_commit(root),
        "engine": engine,
        "file_count": len(file_entries),
        "symbol_count": sum(len(f["symbols"]) for f in file_entries),
        "max_symbols_per_file": max_symbols,
        "files": file_entries,
    }


def render_md(m, max_symbols: int):
    lines = []
    lines.append("# Codebase Map")
    lines.append("")
    lines.append(
        f"_Generated by Gio's `codebase_map.py` (engine: {m['engine']}). "
        f"{m['file_count']} files, {m['symbol_count']} symbols. "
        "Map-then-verify: jump to the file/line below, then Read that span to "
        "confirm before acting — regenerate if it has drifted._")
    if m.get("git_commit"):
        lines.append("")
        lines.append(f"Commit: `{m['git_commit']}`")
    lines.append("")

    # Group files by their top-level directory ("." for repo root).
    groups = {}
    for f in m["files"]:
        top = f["path"].split("/")[0] if "/" in f["path"] else "."
        groups.setdefault(top, []).append(f)

    for top in sorted(groups):
        lines.append(f"## {top}/" if top != "." else "## (root)")
        lines.append("")
        for f in sorted(groups[top], key=lambda x: x["path"]):
            header = f"### `{f['path']}`"
            if f["purpose"]:
                header += f" — {f['purpose']}"
            lines.append(header)
            syms = f["symbols"]
            if not syms:
                lines.append("_(no symbols extracted)_")
                lines.append("")
                continue
            shown = syms[:max_symbols]
            for s in shown:
                span = (f"L{s['line_start']}"
                        if s["line_start"] == s["line_end"]
                        else f"L{s['line_start']}–L{s['line_end']}")
                lines.append(f"- `{s['name']}` · {s['kind']} · {span}")
            if len(syms) > max_symbols:
                lines.append(f"- _… +{len(syms) - max_symbols} more "
                             "(see `.codebase-map.json`)_")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def check_staleness(root: Path):
    """Compare files on disk to the stored map. Returns (drift_bool, report)."""
    json_path = root / JSON_FILENAME
    try:
        stored = json.loads(json_path.read_text())
    except (OSError, json.JSONDecodeError):
        return True, {"error": f"No readable map at {json_path}. Generate one first."}

    stored_files = {f["path"]: f for f in stored.get("files", [])}
    current = set(discover_files(root))
    stored_set = set(stored_files)

    added = sorted(current - stored_set)
    deleted = sorted(stored_set - current)
    changed = []
    for rel in sorted(current & stored_set):
        try:
            sha = short_sha((root / rel).read_bytes())
        except OSError:
            continue
        if sha != stored_files[rel].get("sha"):
            changed.append(rel)

    drift = bool(added or deleted or changed)
    return drift, {
        "added": added, "deleted": deleted, "changed": changed,
        "stored_commit": stored.get("git_commit"),
        "current_commit": git_commit(root),
        "drift": drift,
    }


def find_symbol(root: Path, query: str, limit: int):
    """Return compact locations for a symbol without loading the whole map.

    Prefers the stored .codebase-map.json (cheap); falls back to building the
    map in memory if none exists. Exact name (or method basename) matches win;
    otherwise case-insensitive substring matches. The point is a tiny result an
    agent can act on: locate -> Read that span -> verify.
    """
    m = None
    try:
        m = json.loads((root / JSON_FILENAME).read_text())
    except (OSError, json.JSONDecodeError):
        m = None
    built = m is None
    if m is None:
        m = build_map(root, "auto", DEFAULT_MAX_SYMBOLS_PER_FILE)

    q = query.lower()
    exact, partial = [], []
    for f in m["files"]:
        for s in f["symbols"]:
            name = s["name"]
            base = name.split(".")[-1]
            hit = {"path": f["path"], "name": name, "kind": s["kind"],
                   "line_start": s["line_start"], "line_end": s["line_end"]}
            if name == query or base == query:
                exact.append(hit)
            elif q in name.lower():
                partial.append(hit)

    hits = (exact or partial)[:limit]
    return {"query": query, "from_stored_map": not built,
            "exact": bool(exact), "match_count": len(hits), "matches": hits}


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Generate a labeled, regenerable codebase map (map-then-verify).")
    p.add_argument("--root", default=".",
                   help="Repository root to map (default: current directory)")
    p.add_argument("--engine", default="auto",
                   choices=["auto", "treesitter", "ctags", "ast", "regex"],
                   help="Symbol-extraction engine (default: auto)")
    p.add_argument("--max-symbols-per-file", type=int,
                   default=DEFAULT_MAX_SYMBOLS_PER_FILE,
                   help=f"Cap symbols shown per file in the Markdown view "
                        f"(default: {DEFAULT_MAX_SYMBOLS_PER_FILE})")
    p.add_argument("--find", metavar="SYMBOL",
                   help="Print just the file:line location(s) of a symbol "
                        "(uses the stored map; no whole-map load)")
    p.add_argument("--check", action="store_true",
                   help="Report staleness vs the existing map; exit 1 on drift")
    p.add_argument("--stdout", action="store_true",
                   help="Print the Markdown map to stdout instead of writing files")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of a human-readable report")
    args = p.parse_args(argv)

    root = Path(os.path.expanduser(args.root)).resolve()
    if not root.is_dir():
        msg = f"Not a directory: {root}"
        print(json.dumps({"error": msg}) if args.json else msg, file=sys.stderr)
        return 1

    if args.find:
        res = find_symbol(root, args.find, limit=50)
        if args.json:
            print(json.dumps(res, indent=2))
        elif not res["matches"]:
            print(f"No symbol matching '{args.find}'. Try `--check` (the map may "
                  "be stale) or Grep as a fallback.", file=sys.stderr)
            return 1
        else:
            for h in res["matches"]:
                span = (f"L{h['line_start']}"
                        if h["line_start"] == h["line_end"]
                        else f"L{h['line_start']}-L{h['line_end']}")
                print(f"{h['path']}:{span}  {h['kind']}  {h['name']}")
        return 0 if res["matches"] else 1

    if args.check:
        drift, report = check_staleness(root)
        if "error" in report:
            print(json.dumps(report) if args.json else report["error"],
                  file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            if not drift:
                print("Codebase map is up to date (no drift).")
            else:
                print("Codebase map is STALE — regenerate with "
                      "`python3 scripts/codebase_map.py`:")
                for label, items in (("added", report["added"]),
                                     ("changed", report["changed"]),
                                     ("deleted", report["deleted"])):
                    for it in items:
                        print(f"  {label:8} {it}")
        return 1 if drift else 0

    m = build_map(root, args.engine, args.max_symbols_per_file)

    if args.json:
        print(json.dumps(m, indent=2))
        return 0

    md = render_md(m, args.max_symbols_per_file)
    if args.stdout:
        print(md)
        return 0

    md_path = root / MD_FILENAME
    json_path = root / JSON_FILENAME
    try:
        md_path.write_text(md)
        json_path.write_text(json.dumps(m, indent=2) + "\n")
    except OSError as e:
        print(f"Could not write map: {e}", file=sys.stderr)
        return 1
    print(f"Wrote {md_path}  and  {json_path}")
    print(f"  engine: {m['engine']}  ·  {m['file_count']} files  ·  "
          f"{m['symbol_count']} symbols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
