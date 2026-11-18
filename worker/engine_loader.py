"""Loads the ZIROQ engine cells (worker/engine_src/*.py, taken unchanged from PROJECT_ZIROQ_v6.ipynb)
into ONE shared namespace, exactly like the notebook did.

Two modes:
  load_definitions(ns)  - imports, functions, classes and constant assignments only. No download, no model,
                          no network, no pipeline run. Used by the tests and by quick self-checks.
  load_full(ns, ...)    - executes whole cells in order (a real production run). Patches are applied as
                          exact text replacements that MUST match once, so a changed notebook fails loudly.
"""
import ast
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "engine_src")

# Cells whose definitions the tests load (cell 0 = pip, 0b = Colab form, 3 = Colab Drive are not engine logic).
DEFINITION_CELLS = [
    "02_cell_1.py", "03_cell_2.py", "05_cell_3b.py", "07_cell_4.py", "08_cell_5.py", "09_cell_6.py",
    "10_cell_6b.py", "12_cell_7.py", "13_cell_8.py", "14_cell_9.py", "15_cell_9b.py", "16_cell_10.py",
    "17_cell_10b.py", "18_cell_10c.py", "19_cell_11.py",
]
# Order of a real production run (cells 3c and 6c are the notebook's own fake-client tests and 13 is developer-only).
RUN_CELLS = [
    "02_cell_1.py", "03_cell_2.py", "04_cell_3.py", "05_cell_3b.py", "07_cell_4.py", "08_cell_5.py",
    "09_cell_6.py", "10_cell_6b.py", "12_cell_7.py", "13_cell_8.py", "14_cell_9.py", "15_cell_9b.py",
    "16_cell_10.py", "17_cell_10b.py", "18_cell_10c.py", "19_cell_11.py",
]
TEST_CELLS = ["06_cell_3c.py", "11_cell_6c.py"]


def read_cell(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


def strip_magics(source):
    return "\n".join(l for l in source.split("\n") if not l.lstrip().startswith(("!", "%")))


def apply_replacements(source, replacements, cell_name="?"):
    """Each (old, new) must occur exactly once; anything else means the engine changed and the patch needs review."""
    for old, new in replacements:
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"patch for {cell_name} matched {count} times (expected 1): {old[:70]!r}")
        source = source.replace(old, new)
    return source


def _stub_missing_heavy_modules():
    for mod in ("yt_dlp", "faster_whisper", "ctranslate2"):
        if mod not in sys.modules:
            try:
                __import__(mod)
            except Exception:
                sys.modules[mod] = types.ModuleType(mod)


_SAFE_CALLS = {"join", "get", "compile", "now", "strftime", "uuid4", "dict", "set", "list", "frozenset", "tuple", "Counter",
               "defaultdict", "Lock", "getenv", "dirname", "abspath", "str", "int", "float", "len", "round", "max", "min",
               "sorted", "range", "isoformat", "lower", "upper", "split", "replace", "format", "strip"}


def _only_safe_calls(value):
    for n in ast.walk(value):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in _SAFE_CALLS:
                return False
    return True


def load_definitions(ns=None, cells=None, work_dir=None):
    """Define the engine's functions/classes/constants without running any pipeline step."""
    ns = ns if ns is not None else {"__name__": "ziroq_engine"}
    _stub_missing_heavy_modules()
    for name in (cells or DEFINITION_CELLS):
        source = strip_magics(read_cell(name))
        if work_dir and name == "02_cell_1.py":      # the work dir must be right BEFORE RUN_DIR/CACHE_ROOT are derived from it
            source = apply_replacements(source, [('"work_dir": "/content/ziroq_work",', f'"work_dir": {work_dir!r},')], name)
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Try)):
                is_try_ok = not isinstance(node, ast.Try) or all(
                    isinstance(n, (ast.Import, ast.ImportFrom, ast.Assign, ast.Pass)) for n in node.body)
                if not is_try_ok:
                    continue
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if node.value is None or not _only_safe_calls(node.value):
                    continue                 # assignments that RUN something (input, pipeline steps) are never executed here
            else:
                continue                     # expressions (print, run_something()) are skipped on purpose
            mod = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(mod)
            try:
                exec(compile(mod, name, "exec"), ns)
            except Exception:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    raise                    # a definition must always load
                # an assignment that needs runtime state (a transcript, a Drive folder) is simply left undefined
    return ns


def load_full(ns, cells=None, patches=None, after=None):
    """Run whole cells in order. patches: {cell_name: [(old, new), ...]}; after: {cell_name: callable(ns)}."""
    for name in (cells or RUN_CELLS):
        src = strip_magics(read_cell(name))
        if patches and name in patches:
            p = patches[name]
            src = p(src) if callable(p) else apply_replacements(src, p, name)
        exec(compile(src, name, "exec"), ns)
        if after and name in after:
            after[name](ns)
    return ns
