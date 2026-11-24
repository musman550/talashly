"""Python code ko mehfooz tareeqe se chalana (agent ke "likho aur chala kar parakho" ke liye).

Teen parat: (1) AST check - sirf whitelist imports, koi dunder/attribute-escape, koi open/exec/eval;
(2) alag process: python -I, khali environment, tempdir, CPU/RAM/file rlimits, wall-clock timeout;
(3) output cap. Network/file/subprocess modules whitelist mein hain hi nahi.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile

ALLOWED = {"math", "statistics", "itertools", "collections", "re", "json", "datetime", "random", "fractions", "decimal",
           "string", "functools", "heapq", "bisect", "typing", "dataclasses", "operator", "textwrap", "enum", "copy", "time"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals", "vars", "getattr",
                "setattr", "delattr", "dir", "memoryview", "exit", "quit", "help"}
MAX_OUT = 2000
MAX_CODE = 6000

_RUNNER = r'''
import sys, builtins, resource
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
resource.setrlimit(resource.RLIMIT_AS, (400 * 1024 * 1024, 400 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
ALLOWED = %r
real_import = builtins.__import__
def imp(name, g=None, l=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED:
        raise ImportError("module not allowed: " + name)
    return real_import(name, g, l, fromlist, level)
safe = {k: getattr(builtins, k) for k in dir(builtins) if k not in %r and not k.startswith("_")}
safe["__import__"] = imp
safe["__build_class__"] = builtins.__build_class__
safe["__name__"] = "__main__"
code = sys.stdin.read()
exec(compile(code, "<agent>", "exec"), {"__builtins__": safe, "__name__": "__main__"})
'''


def check(code: str) -> str | None:
    """None = theek, warna wajah."""
    if len(code) > MAX_CODE:
        return "code too long"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"syntax error: {e.msg} (line {e.lineno})"
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] not in ALLOWED:
                    return f"import not allowed: {a.name}"
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] not in ALLOWED or n.level:
                return f"import not allowed: {n.module}"
        elif isinstance(n, ast.Attribute) and n.attr.startswith("_"):
            return f"attribute not allowed: {n.attr}"
        elif isinstance(n, ast.Name) and (n.id in BANNED_NAMES or (n.id.startswith("__") and n.id != "__name__")):
            return f"name not allowed: {n.id}"
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            return "global/nonlocal not allowed"
    return None


def run_python(code: str, timeout: float = 4.0) -> dict:
    """{'ok': bool, 'out': str, 'err': str, 'blocked': bool}"""
    why = check(code)
    if why:
        return {"ok": False, "out": "", "err": why, "blocked": True}

    def limits():  # child: nayi session, kam priority
        os.setsid()
        os.nice(10)

    with tempfile.TemporaryDirectory() as td:
        try:
            p = subprocess.run([sys.executable, "-I", "-c", _RUNNER % (sorted(ALLOWED), sorted(BANNED_NAMES))], input=code, text=True,
                               capture_output=True, timeout=timeout, cwd=td, env={"PYTHONHASHSEED": "0"}, preexec_fn=limits)
        except subprocess.TimeoutExpired:
            return {"ok": False, "out": "", "err": f"timed out after {timeout:.0f}s", "blocked": False}
        except Exception as e:  # noqa: BLE001  (preexec/fork/limits ki dikkat)
            return {"ok": False, "out": "", "err": f"sandbox error {type(e).__name__}: {e}"[:200], "blocked": True}
    err = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ""
    if p.returncode < 0 and not err:
        err = "stopped: CPU/time limit"
    return {"ok": p.returncode == 0, "out": p.stdout[:MAX_OUT], "err": err[:300], "blocked": False}
