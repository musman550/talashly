import json
import sys

raw = open(sys.argv[1], encoding="utf8", errors="replace").read()
try:
    lines = [e.get("data", "").rstrip() for e in json.loads(raw)]
except Exception:  # noqa: BLE001
    lines = raw.splitlines()
keep = [x for x in lines if x.strip()]
print("lines:", len(keep))
for x in keep[:14] + ["..."] + keep[-22:]:
    print(x[:220])
