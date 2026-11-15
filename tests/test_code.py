import asyncio

import pytest

from engine.brain import Brain
from engine.codeindex import index_paths
from engine.db import DB
from engine.search import Searcher, split_identifiers
from tests.helpers import collect, make_cfg


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "util.py").write_text(
        "import requests\n\ndef fetch_user_profile(user_id):\n    \"\"\"Download the profile of a user.\"\"\"\n"
        "    return requests.get(f'/users/{user_id}').json()\n", encoding="utf-8")
    (root / "src" / "app.js").write_text(
        "export async function fetchUserProfile(id) {\n  const r = await fetch(`/users/${id}`);\n  return r.json();\n}\n", encoding="utf-8")
    (root / "src" / "Main.java").write_text(
        "public class UserService {\n  public User getUserById(long id) { return repo.find(id); }\n}\n", encoding="utf-8")
    (root / "src" / "urdu.py").write_text("# یہ فنکشن صارف کا ڈیٹا واپس کرتا ہے\ndef get_data():\n    return 1\n", encoding="utf-8")
    (root / "node_modules" / "lib" / "skip.js").write_text("function fetchUserProfile(){}  // must be skipped", encoding="utf-8")
    (root / ".git" / "x.py").write_text("def fetch_user_profile(): pass", encoding="utf-8")
    (root / "src" / "binary.py").write_bytes(b"\x00\x01\x02def fetch_user_profile(): pass")
    (root / "src" / "huge.py").write_text("x = 1\n" * 200_000, encoding="utf-8")  # >500KB
    (root / "notes.txt").write_text("fetch_user_profile in an unsupported extension", encoding="utf-8")
    return root


@pytest.fixture()
def indexed(project, tmp_path):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    stats = index_paths(db, [str(project)], 500)
    return cfg, db, project, stats


def test_indexes_supported_files_and_skips_junk(indexed):
    cfg, db, root, stats = indexed
    paths = {r["path"].replace(str(root.resolve()), "") for r in db.q("SELECT path FROM code_files")}
    assert paths == {"/src/util.py", "/src/app.js", "/src/Main.java", "/src/urdu.py"}
    assert stats["indexed"] == 4 and stats["skipped"] >= 2  # binary + huge


def test_search_by_exact_identifier_and_by_words(indexed):
    cfg, db, root, _ = indexed
    s = Searcher(db, cfg)
    assert [h.path.endswith("util.py") for h in s.search_code("fetch_user_profile")][0]
    both = {h.path.rsplit("/", 1)[1] for h in s.search_code("fetch user profile")}
    assert {"util.py", "app.js"} <= both  # snake_case aur camelCase dono, alfaaz se
    assert "skip.js" not in both


def test_camel_case_query_finds_snake_case_code(indexed):
    cfg, db, root, _ = indexed
    paths = {h.path.rsplit("/", 1)[1] for h in Searcher(db, cfg).search_code("fetchUserProfile")}
    assert {"util.py", "app.js"} <= paths


def test_language_filter(indexed):
    cfg, db, root, _ = indexed
    hits = Searcher(db, cfg).search_code("fetch user profile", lang="javascript")
    assert hits and all(h.lang == "javascript" for h in hits)


def test_non_english_comments_searchable(indexed):
    cfg, db, root, _ = indexed
    assert Searcher(db, cfg).search_code("صارف")  # urdu comment


def test_incremental_unchanged_changed_deleted(indexed):
    cfg, db, root, _ = indexed
    s2 = index_paths(db, [str(root)], 500)
    assert s2["indexed"] == 0 and s2["unchanged"] == 4 and s2["removed"] == 0

    (root / "src" / "util.py").write_text("def brand_new_function_name():\n    return 42\n", encoding="utf-8")
    s3 = index_paths(db, [str(root)], 500)
    assert s3["indexed"] == 1 and s3["unchanged"] == 3
    s = Searcher(db, cfg)
    assert s.search_code("brand_new_function_name")
    assert not [h for h in s.search_code("fetch_user_profile") if h.path.endswith("util.py")]  # purana chala gaya

    (root / "src" / "Main.java").unlink()
    s4 = index_paths(db, [str(root)], 500)
    assert s4["removed"] == 1 and not s.search_code("UserService")
    n = db.q("SELECT COUNT(*) FROM code_chunks")[0][0]
    assert n == db.q("SELECT COUNT(*) FROM code_fts")[0][0]  # FTS aur table hamesha barabar


def test_missing_path_is_harmless(tmp_path):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    assert index_paths(db, [str(tmp_path / "nope")], 500) == {"indexed": 0, "unchanged": 0, "removed": 0, "skipped": 0}


@pytest.mark.parametrize("q", ['"', "'", "AND OR NOT", "NEAR(a b)", "a*", "()", "fetch_user_profile; DROP TABLE docs;--",
                               "\\", "💥", "x" * 400, "SELECT * FROM code_files"])
def test_hostile_code_queries_never_crash(indexed, q):
    cfg, db, root, _ = indexed
    Searcher(db, cfg).search_code(q)  # exception nahi aani chahiye
    assert db.q("SELECT COUNT(*) FROM code_files")[0][0] == 4  # table salamat


def test_split_identifiers():
    assert split_identifiers("fetchUserProfile") == "fetch user profile"
    assert split_identifiers("fetch_user_profile") == "fetch user profile"
    assert split_identifiers("HTTPServerError") == "http server error"
    assert split_identifiers("parseJSON2Html") == "parse json 2 html"


def test_brain_routes_code_questions_to_code_index(indexed):
    cfg, db, root, _ = indexed
    brain = Brain(cfg, db, Searcher(db, cfg), [])
    r = asyncio.run(collect(brain, "code: fetch user profile"))
    assert r["plan"]["intent"] == "code" and r["code"]
    assert "util.py" in r["text"] or "app.js" in r["text"]
    # python filter sentence se
    r2 = asyncio.run(collect(brain, "python def fetch_user_profile(user_id) kaise likhte hain"))
    assert r2["plan"]["intent"] == "code"


@pytest.mark.parametrize("q,expected", [
    ("What is the capital of France?", "question"),
    ("latest news about Pakistan economy", "question"),
    ("code: sort a list", "code"),
    ("```python\nprint(1)\n```", "code"),
    ("Traceback (most recent call last): Exception in fetch_data()", "code"),
    ("def fetch_data(self): raise ValueError", "code"),
    ("what is the difference between a function and a method", "question"),
    ("how does the stock market work", "question"),
    ("what is python", "question"),
    ("python vs java popularity 2026", "question"),
    ("how to install python on windows", "question"),
    ("history of the C language", "question"),
    ("python def fetch_user_profile(user_id) kaise likhte hain", "code"),
])
def test_intent_classification(tmp_path, q, expected):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    assert Brain(cfg, db, Searcher(db, cfg), []).classify(q) == expected
