# Kaggle GPU par chalne wali script (GitHub Actions `kaggle-train` ise push karta hai). Khud-ba-khud:
#   1) teacher (Qwen3-8B 4-bit) passages se sawal+sahi jawab banata hai, aur Talashly ke persona mein open tasks ke jawab
#   2) student (Qwen3-1.7B) par LoRA fine-tune, usi prompt-format mein jo runtime mein chalta hai
#   3) merge -> GGUF Q4_K_M  => /kaggle/working/talashly-qwen3-1.7b-Q4_K_M.gguf
# Har stage alag try/except mein; /kaggle/working/status.json aur log.txt hamesha likhe jate hain.
import glob
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback

W = "/kaggle/working"
T0 = time.time()
TEACHER = os.environ.get("TEACHER", "Qwen/Qwen3-8B")
STUDENT = os.environ.get("STUDENT", "Qwen/Qwen3-1.7B")
BUDGET_TEACHER_S = int(os.environ.get("BUDGET_TEACHER_MIN", "100")) * 60
BUDGET_TRAIN_S = int(os.environ.get("BUDGET_TRAIN_MIN", "80")) * 60
N_GROUNDED = int(os.environ.get("N_GROUNDED", "1500"))
N_DIRECT = int(os.environ.get("N_DIRECT", "450"))
STATUS = {"stages": {}}


def log(*a):
    line = f"[{(time.time() - T0) / 60:6.1f}m] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(f"{W}/log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save_status():
    with open(f"{W}/status.json", "w") as f:
        json.dump(STATUS, f, indent=1)


def sh(cmd):
    log("$", cmd[:160])
    subprocess.run(cmd, shell=True, check=True)


def stage(name):
    def deco(fn):
        def run(*a, **k):
            t = time.time()
            try:
                r = fn(*a, **k)
                STATUS["stages"][name] = {"ok": True, "min": round((time.time() - t) / 60, 1)}
                return r
            except Exception as e:  # noqa: BLE001
                log(f"STAGE {name} FAILED: {e}\n{traceback.format_exc()[-1500:]}")
                STATUS["stages"][name] = {"ok": False, "error": str(e)[:300]}
                return None
            finally:
                save_status()
        return run
    return deco


@stage("install")
def install():
    sh("pip install -q -U 'transformers>=4.51' peft accelerate bitsandbytes gguf sentencepiece protobuf httpx")
    sh(f"git clone --depth 1 https://github.com/musman550/talashly {W}/repo")


def system_prompt():
    """Runtime wala SYSTEM_PROMPT repo se (engine.chat import kiye bina: uski dependencies Kaggle par nahi chahiye)."""
    import ast
    tree = ast.parse(open(f"{W}/repo/engine/chat.py", encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "SYSTEM_PROMPT" for t in n.targets):
            return ast.literal_eval(n.value)
    raise RuntimeError("SYSTEM_PROMPT nahi mila")


def load_passages():
    f = (glob.glob("/kaggle/input/**/passages.jsonl", recursive=True) or [None])[0]
    if not f:
        raise RuntimeError("passages.jsonl dataset mein nahi mila")
    rows = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
    random.Random(7).shuffle(rows)
    log(len(rows), "passages")
    return rows


def make_generator(model_id, four_bit):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(model_id, padding_side="left")
    kw = dict(device_map={"": 0})
    if four_bit:
        kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_quant_type="nf4")
    else:
        kw["torch_dtype"] = torch.float16
    model = AutoModelForCausalLM.from_pretrained(model_id, **kw)
    model.eval()

    def gen(msgs_list, max_new):
        texts = [tok.apply_chat_template(m, tokenize=False, add_generation_prompt=True, enable_thinking=False) for m in msgs_list]
        enc = tok(texts, return_tensors="pt", padding=True).to("cuda:0")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=max_new, do_sample=True, temperature=0.7, top_p=0.9, pad_token_id=tok.pad_token_id)
        res = [tok.decode(o[enc.input_ids.shape[1]:], skip_special_tokens=True) for o in out]
        return [re.sub(r"<think>.*?</think>", "", r, flags=re.S).strip() for r in res]

    return gen, model


LANG_NAME = {"en": "English", "roman": "Roman Urdu (Urdu written in English letters)", "ur": "Urdu (Arabic script)"}


@stage("teacher")
def teacher(passages):
    sys.path.insert(0, f"{W}/repo")
    from engine.autopilot import mutate, words
    SYSTEM_PROMPT = system_prompt()
    from engine import trainprep as tp
    rng = random.Random(11)
    gen, model = make_generator(TEACHER, True)
    t0 = time.time()
    ex = []
    # ---- grounded: passage -> (sawal, jawab)
    B = 8
    pool = passages[:N_GROUNDED]
    for i in range(0, len(pool), B):
        if time.time() - t0 > BUDGET_TEACHER_S * 0.6:
            log("grounded: time budget khatam")
            break
        batch = pool[i:i + B]
        langs = [rng.choices(["en", "roman", "ur"], [6, 2, 2])[0] for _ in batch]
        msgs = [[{"role": "user", "content":
                  f"Passage:\n{p['passage']}\n\nWrite ONE realistic question a user could ask whose answer is stated in this passage, "
                  f"and a concise answer (1-3 sentences) using ONLY facts from the passage. Write both in {LANG_NAME[l]}. "
                  'Reply ONLY with JSON: {"question": "...", "answer": "..."}'}] for p, l in zip(batch, langs)]
        for p, raw in zip(batch, gen(msgs, 200)):
            d = tp.parse_teacher_json(raw)
            if not d or not tp.grounded(d["answer"], p["passage"]):
                continue
            q = d["question"]
            if rng.random() < 0.5:
                q = mutate(q, rng.choice([1, 2, 3]), words(q))
            others = [x for x in rng.sample(passages, 6) if x["url"] != p["url"]][:2]
            ex.append(tp.grounded_example(q, d["answer"], p, others, rng))
        if (i // B) % 10 == 0:
            log(f"grounded {len(ex)} examples")
    n_g = len(ex)
    for q in tp.unknown_questions(passages, max(30, n_g // 5), rng):
        ex.append(tp.unknown_example(q, [x for x in rng.sample(passages, 3)]))
    # ---- direct: persona + policy (poem, email, compare, code, website, harm)
    prompts = tp.direct_prompts(N_DIRECT)
    big = [p for p in prompts if re.search(r"website|landing page", p, re.I)]
    small = [p for p in prompts if p not in big]
    for group, bs, mx in ((small, 8, 420), (big, 2, 1500)):
        for i in range(0, len(group), bs):
            if time.time() - t0 > BUDGET_TEACHER_S:
                break
            batch = group[i:i + bs]
            msgs = [[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": q}] for q in batch]
            for q, a in zip(batch, gen(msgs, mx)):
                harm = any(h[:14].lower() in q.lower() for h in tp.HARM)
                refuses = bool(re.search(r"\b(can't|cannot|won't|not able|sorry|nahi kar sakta|i can’t)\b", a, re.I))
                if not a or "<think>" in a or (harm != refuses and (harm or refuses)):
                    continue
                if "website" in q.lower() and "```html" not in a:
                    continue
                ex.append(tp.direct_example(SYSTEM_PROMPT, q, a))
    log("teacher examples:", len(ex), {k: sum(1 for e in ex if e['kind'] == k) for k in ("grounded", "unknown", "direct")})
    random.Random(3).shuffle(ex)
    with open(f"{W}/training_data.jsonl", "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in ex))
    del gen, model  # gen ke closure mein bhi model hai: dono hatao tabhi GPU memory khali hoti hai
    import gc, torch
    gc.collect(); torch.cuda.empty_cache()
    return ex


@stage("train")
def train(ex):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(STUDENT)
    model = AutoModelForCausalLM.from_pretrained(STUDENT, torch_dtype=torch.float16, device_map={"": 0})
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    for p in model.parameters():
        if p.requires_grad:
            p.data = p.data.float()

    def enc(e):
        m = e["messages"]
        prompt = tok.apply_chat_template(m[:-1], tokenize=False, add_generation_prompt=True)
        full = prompt + "<think>\n\n</think>\n\n" + m[-1]["content"] + "<|im_end|>\n"  # runtime mein Qwen3 /no_think yahi likhta hai
        ids = tok(full).input_ids
        if len(ids) > 1536:
            return None
        plen = len(tok(prompt).input_ids)
        return torch.tensor([ids]), torch.tensor([[-100] * plen + ids[plen:]])
    data = [x for x in (enc(e) for e in ex) if x]
    cut = max(1, len(data) // 20)
    val, tr = data[:cut], data[cut:]
    log("train/val", len(tr), len(val))
    def vloss():
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            return sum(model(input_ids=a.to("cuda:0"), labels=b.to("cuda:0")).loss.item() for a, b in val) / len(val)
    model.eval()
    with model.disable_adapter():   # eval gate: asli (base) model ka held-out loss, training se pehle
        base_vl = vloss()
    STATUS["base_val_loss"] = round(base_vl, 4)
    log("base val loss", base_vl)
    model.train()
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=2e-4, weight_decay=0.0)
    scaler = torch.cuda.amp.GradScaler()
    ACC, EPOCHS = 16, 2
    total = max(1, EPOCHS * len(tr) // ACC)
    step, t0 = 0, time.time()
    model.train()
    for ep in range(EPOCHS):
        random.Random(ep).shuffle(tr)
        for i, (ids, lab) in enumerate(tr):
            with torch.autocast("cuda", dtype=torch.float16):
                loss = model(input_ids=ids.to("cuda:0"), labels=lab.to("cuda:0")).loss / ACC
            scaler.scale(loss).backward()
            if (i + 1) % ACC == 0:
                lr = 2e-4 * min(1.0, (step + 1) / 10) * max(0.05, 1 - step / total)
                for g in opt.param_groups:
                    g["lr"] = lr
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                scaler.step(opt); scaler.update(); opt.zero_grad()
                step += 1
                if step % 10 == 0:
                    log(f"ep{ep} step {step}/{total} loss {loss.item() * ACC:.3f}")
            if time.time() - t0 > BUDGET_TRAIN_S:
                break
        if time.time() - t0 > BUDGET_TRAIN_S:
            log("train: time budget khatam")
            break
    model.eval()
    vl = vloss()
    STATUS["val_loss"] = round(vl, 4)
    STATUS["gate"] = "pass" if vl < base_vl * 0.98 else "rejected"   # kam se kam 2% behtar na ho to release nahi
    log("val loss", vl, "gate", STATUS["gate"])
    if STATUS["gate"] != "pass":
        return
    model.save_pretrained(f"{W}/lora")
    merged = model.merge_and_unload()
    merged.save_pretrained(f"{W}/merged", safe_serialization=True)
    tok.save_pretrained(f"{W}/merged")


@stage("gguf")
def gguf():
    sh(f"git clone --depth 1 https://github.com/ggml-org/llama.cpp {W}/llama.cpp")
    sh(f"python {W}/llama.cpp/convert_hf_to_gguf.py {W}/merged --outfile {W}/f16.gguf --outtype f16")
    sh(f"cd {W}/llama.cpp && cmake -B build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF > /dev/null && cmake --build build --target llama-quantize -j4 > /dev/null")
    sh(f"{W}/llama.cpp/build/bin/llama-quantize {W}/f16.gguf {W}/talashly-qwen3-1.7b-Q4_K_M.gguf Q4_K_M")
    STATUS["gguf_mb"] = round(os.path.getsize(f"{W}/talashly-qwen3-1.7b-Q4_K_M.gguf") / 1e6)


def cleanup():
    for p in ("merged", "f16.gguf", "llama.cpp", "repo"):
        subprocess.run(f"rm -rf {W}/{p}", shell=True)


if __name__ == "__main__":
    os.makedirs(W, exist_ok=True)
    install()
    passages = stage("load")(load_passages)()
    examples = teacher(passages) if passages else None
    if examples and len(examples) >= 100:
        train(examples)
        if STATUS["stages"].get("train", {}).get("ok") and STATUS.get("gate") == "pass":
            gguf()
        elif STATUS.get("gate") == "rejected":
            log("eval gate: naya model base se behtar nahi, release nahi hoga (purana model rehta hai)")
    else:
        log("teacher se kam examples (<100): training chhodi")
    cleanup()
    STATUS["total_min"] = round((time.time() - T0) / 60, 1)
    save_status()
    log("done", json.dumps(STATUS))
