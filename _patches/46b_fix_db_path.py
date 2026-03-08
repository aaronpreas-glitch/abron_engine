#!/usr/bin/env python3
"""Fix db path in the 5 new endpoints — replace pathlib with os.path pattern."""
import pathlib

FILE = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
content = FILE.read_text()

# Replace all instances of the pathlib pattern with the os.path pattern
old = '''        _ensure_engine_path()
        import sqlite3
        db = str(pathlib.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")'''
new = '''        import sqlite3
        db = os.path.join(_engine_root(), "data_storage", "engine.db")'''

count = content.count(old)
assert count > 0, f"FAIL: pathlib pattern not found (found {count})"
content = content.replace(old, new)
print(f"Fixed {count} endpoint(s) with pathlib -> os.path")

# Also fix the one with sqlite3, json import
old2 = '''        _ensure_engine_path()
        import sqlite3, json
        db = str(pathlib.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")'''
new2 = '''        import sqlite3, json
        db = os.path.join(_engine_root(), "data_storage", "engine.db")'''

count2 = content.count(old2)
if count2 > 0:
    content = content.replace(old2, new2)
    print(f"Fixed {count2} endpoint(s) with json import variant")

FILE.write_text(content)

import py_compile
py_compile.compile(str(FILE), doraise=True)
print("\n✅ DB path fix applied, file compiles OK")
