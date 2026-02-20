import os
import glob
import ast
import collections

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

py_files = [
    p for p in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True)
    if "__pycache__" not in p and ".venv" not in p
]


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


module_of_file = {}
for file_path in py_files:
    rel_path = rel(file_path)
    stem = rel_path[:-3]
    if stem.endswith("/__init__"):
        stem = stem[:-9]
    module_of_file[file_path] = stem.replace("/", ".")

refs = collections.defaultdict(set)
for file_path in py_files:
    rel_path = rel(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except Exception:
        continue

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs[alias.name].add(rel_path)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if node.level and not module_name:
                continue
            refs[module_name].add(rel_path)

inbound = collections.defaultdict(set)
known_modules = set(module_of_file.values())
for imported_name, users in refs.items():
    parts = imported_name.split(".") if imported_name else []
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in known_modules:
            inbound[candidate].update(users)
            break

no_inbound = []
for file_path, module_name in module_of_file.items():
    rel_path = rel(file_path)
    if rel_path.startswith("tests/") or rel_path == "main.py":
        continue
    users = {u for u in inbound.get(module_name, set()) if u != rel_path}
    if not users:
        no_inbound.append(rel_path)

print(f"NO_INBOUND_COUNT {len(no_inbound)}")
for item in sorted(no_inbound):
    print(item)
