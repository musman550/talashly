import pytest

from engine.db import DB, sha
from engine.extract import chunk_text
from engine.search import Searcher
from tests.helpers import make_cfg


@pytest.fixture()
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    return cfg, DB(cfg["general"]["db_path"])


def up(db, url, text, title="T"):
    return db.upsert_doc(url, title, None, None, "t", None, sha(text), chunk_text(text))


def test_upsert_new_unchanged_and_changed(env):
    cfg, db = env
    id1, ch1 = up(db, "http://a.test/x", "alpha text about crawlers " * 30)
    id2, ch2 = up(db, "http://a.test/x", "alpha text about crawlers " * 30)
    assert ch1 is True and ch2 is False and id1 == id2
    id3, ch3 = up(db, "http://a.test/x", "beta text about gardens " * 30)
    assert ch3 is True and id3 == id1
    s = Searcher(db, cfg)
    assert s.search("gardens") and not s.search("crawlers")
    assert db.counts()["docs"] == 1 and db.integrity()["ok"]


def test_upsert_works_inside_an_outer_transaction(env):
    cfg, db = env
    db.x("BEGIN")
    for i in range(50):
        up(db, f"http://a.test/{i}", f"document {i} body text " * 30)
    db.x("COMMIT")
    assert db.counts()["docs"] == 50 and db.integrity()["ok"]
    db.x("BEGIN")
    up(db, "http://a.test/rolled-back", "will not survive " * 30)
    db.x("ROLLBACK")                                       # bahar ki transaction ka rollback sab hata deta hai
    assert db.counts()["docs"] == 50


def test_failed_upsert_leaves_no_partial_document(env):
    cfg, db = env
    up(db, "http://a.test/keep", "keep this document " * 30)
    with pytest.raises(Exception):
        db.upsert_doc("http://a.test/bad", "Bad", None, None, "t", None, "h", ["fine chunk " * 20, object()])  # 2nd chunk insert fail
    assert not db.q("SELECT 1 FROM docs WHERE url='http://a.test/bad'")
    assert not db.q("SELECT 1 FROM chunks WHERE text LIKE 'fine chunk%'")
    assert db.counts()["docs"] == 1 and db.integrity()["ok"]
    up(db, "http://a.test/after", "still works after failure " * 30)   # connection saaf haalat mein
    assert db.counts()["docs"] == 2


def test_failed_reindex_keeps_the_old_version(env):
    cfg, db = env
    up(db, "http://a.test/p", "original content stays " * 30)
    with pytest.raises(Exception):
        db.upsert_doc("http://a.test/p", "T", None, None, "t", None, "newhash", ["brandnewtoken " * 20, object()])
    assert Searcher(db, cfg).search("original") and not Searcher(db, cfg).search("brandnewtoken")
    assert db.integrity()["ok"]


def test_cjk_trigram_rows_follow_doc_lifecycle(env):
    cfg, db = env
    up(db, "http://a.test/zh", "异步编程是一种并发模型，事件循环在单个线程上协作地调度协程。" * 8)
    assert db.q("SELECT COUNT(*) FROM chunks_tri")[0][0] == 1
    up(db, "http://a.test/zh", "完全不同的内容，讲述数据库索引与全文检索的原理和实现方法。" * 8)
    assert db.q("SELECT COUNT(*) FROM chunks_tri")[0][0] == 1          # purani trigram row hati, naya aaya
    s = Searcher(db, cfg)
    assert s.search("全文检索") and not s.search("异步编程")


def test_frontier_add_url_dedups_and_normalizes(env):
    cfg, db = env
    assert db.add_url("HTTP://Example.com/a?utm_source=x#top") is True
    assert db.add_url("http://example.com/a") is False
    assert db.add_url("ftp://example.com/a") is False and db.add_url("javascript:1") is False
    assert db.q("SELECT COUNT(*) FROM frontier")[0][0] == 1


def test_due_orders_feeds_first_then_shallow_and_respects_schedule(env):
    cfg, db = env
    db.add_url("http://a.test/deep", 3)
    db.add_url("http://a.test/shallow", 0)
    db.add_url("http://a.test/feed.xml", 1, "feed")
    db.add_url("http://a.test/later", 0, due_now=False)
    order = [r["url"].rsplit("/", 1)[1] for r in db.due(10)]
    assert order == ["feed.xml", "shallow", "deep"]                    # 'later' abhi due nahi
