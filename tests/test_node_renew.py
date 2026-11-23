import json
import pathlib
import types

SRC = pathlib.Path(__file__).resolve().parent.parent / "kaggle" / "node" / "node.py"


def load(**sub):
    text = SRC.read_text(encoding="utf8")
    for k, v in {"__SITE__": "http://x", "__SECRET__": "s", "__MODEL_URL__": "", "__HOURS__": "10.5", "__SLUG__": "talashly-node-a",
                 "__KUSER__": "u", "__KKEY__": "k", **sub}.items():
        text = text.replace(k, v)
    mod = types.ModuleType("node_under_test")
    exec(compile(text, "node.py", "exec"), mod.__dict__)
    return mod, text


def test_successor_swaps_only_the_slot_name():
    mod, text = load()
    src2, meta, other = mod.render_successor(text, "talashly-node-a", "u")
    assert other == "talashly-node-b" and 'NAME = "talashly-node-b"' in src2 and 'NAME = "talashly-node-a"' not in src2
    assert src2.replace("talashly-node-b", "talashly-node-a", 1) == text or src2.count('NAME = "') == 1
    m = json.loads(meta)
    assert m["id"] == "u/talashly-node-b" and m["enable_gpu"] == "false" and m["code_file"] == "node.py"
    a2, _, o2 = mod.render_successor(src2, "talashly-node-b", "u")
    assert o2 == "talashly-node-a" and 'NAME = "talashly-node-a"' in a2


def test_gpu_node_never_renews_and_missing_credentials_disable_renewal():
    mod, text = load(__SLUG__="talashly-gpu")
    assert mod.render_successor(text, "talashly-gpu", "u") is None
    mod2, _ = load(__KKEY__="__KKEY__")     # placeholder bharna chhoot gaya
    calls = []
    mod2.sh = lambda *a, **k: calls.append(a)
    mod2.renew_once()
    assert calls == []
