#!/usr/bin/env bash
# GitHub Action se har 10 minute: Oracle Always-Free A1 (4 core / 24 GB) banane ki koshish. "Out of capacity" aam hai -> chup-chaap
# agli baar. Ban jaye to dobara nahi banata. Env: OCI_USER OCI_TENANCY OCI_FINGERPRINT OCI_REGION OCI_KEY SSH_PUBKEY MESH_SECRET
set -uo pipefail
for v in OCI_USER OCI_TENANCY OCI_FINGERPRINT OCI_REGION OCI_KEY SSH_PUBKEY MESH_SECRET; do
  [ -n "${!v:-}" ] || { echo "::warning::secret $v nahi mila - A1 skip"; exit 0; }
done
mkdir -p ~/.oci
# Windows se paste: CRLF, upar-neeche khali jagah, ya ek hi line ho sakti hai -> key ko saaf, 64-char lines mein dobara likho
printf '%s' "$OCI_KEY" | python3 -c "
import re,sys,textwrap
k=sys.stdin.read().replace('\r','')
m=re.search(r'-----BEGIN ([A-Z ]*PRIVATE KEY)-----(.*?)-----END \1-----',k,re.S)
if not m: sys.exit(3)
b=re.sub(r'\s+','',m.group(2))
print('-----BEGIN %s-----'%m.group(1)); print('\n'.join(textwrap.wrap(b,64))); print('-----END %s-----'%m.group(1))
" > ~/.oci/key.pem
rc=$?
chmod 600 ~/.oci/key.pem
if [ $rc -ne 0 ] || ! openssl pkey -in ~/.oci/key.pem -noout 2>/dev/null; then
  echo "::error::OCI_KEY secret mein sahi private key nahi hai. Wo '-----BEGIN PRIVATE KEY-----' se shuru aur '-----END PRIVATE KEY-----' par khatam hona chahiye (Oracle se download hui .pem file ka poora text)."; exit 1
fi
export SUPPRESS_LABEL_WARNING=True
cat > ~/.oci/config <<CFG
[DEFAULT]
user=$OCI_USER
fingerprint=$OCI_FINGERPRINT
tenancy=$OCI_TENANCY
region=$OCI_REGION
key_file=$HOME/.oci/key.pem
CFG
export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True
T="$OCI_TENANCY"
ERR=/tmp/oci.err; : > "$ERR"
J() { oci "$@" --output json 2>>"$ERR"; }   # stdout = sirf JSON; warnings/errors alag file mein (JSON kharab na ho)

out=$(J compute instance list --compartment-id "$T" --all) || { echo "::error::OCI login/list fail (key/fingerprint/region check karo): $(tail -c 500 "$ERR") $(echo "$out" | head -c 300)"; exit 1; }
if echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data',[]); sys.exit(0 if any(i['shape']=='VM.Standard.A1.Flex' and i['lifecycle-state'] not in ('TERMINATED','TERMINATING') for i in d) else 1)"; then
  echo "A1 VM pehle se maujood hai - kuch nahi karna"; exit 0
fi

subnet=$(J network subnet list --compartment-id "$T" --all | python3 -c "
import sys,json
d=json.load(sys.stdin).get('data',[])
d=[s for s in d if not s.get('prohibit-public-ip-on-vnic') and s['lifecycle-state']=='AVAILABLE']
print(d[0]['id'] if d else '')")
[ -n "$subnet" ] || { echo "::error::public subnet nahi mili (VCN banao ya pehli VM wali subnet chahiye)"; exit 1; }
image=""
for ver in 22.04 24.04 ""; do
  if [ -n "$ver" ]; then vf=(--operating-system-version "$ver"); else vf=(); fi
  image=$(J compute image list --compartment-id "$T" --operating-system "Canonical Ubuntu" "${vf[@]}" \
    --shape VM.Standard.A1.Flex --sort-by TIMECREATED --sort-order DESC --limit 1 | python3 -c "
import sys,json
t=sys.stdin.read().strip()
d=json.loads(t).get('data',[]) if t else []
print(d[0]['id'] if d else '')" 2>/dev/null)
  [ -n "$image" ] && { echo "image: Ubuntu ${ver:-latest}"; break; }
done
[ -n "$image" ] || { echo "::error::Ubuntu ARM image nahi mili: $(tail -c 400 "$ERR")"; exit 1; }

printf '%s\n' "$SSH_PUBKEY" > /tmp/key.pub
sed "s|__MESH_SECRET__|$MESH_SECRET|" deploy/a1-cloud-init.sh > /tmp/init.sh

ADS=$(J iam availability-domain list --compartment-id "$T" | python3 -c "import sys,json; print(' '.join(a['name'] for a in json.load(sys.stdin)['data']))")
LIMIT_MSG=""
for cfg in "4 24" "2 12" "1 6"; do   # account ki A1 limit kam ho to chhoti VM bhi theek (chhoti hui to baad mein badh nahi sakti, isliye bari pehle)
set -- $cfg; OC=$1; MEM=$2
LIMIT_HIT=0
for ad in $ADS; do
  echo "try AD $ad"
  res=$(oci compute instance launch --availability-domain "$ad" --compartment-id "$T" --shape VM.Standard.A1.Flex \
    --shape-config '{"ocpus":'$OC',"memoryInGBs":'$MEM'}' --subnet-id "$subnet" --image-id "$image" --assign-public-ip true \
    --boot-volume-size-in-gbs 100 --ssh-authorized-keys-file /tmp/key.pub --user-data-file /tmp/init.sh \
    --display-name talashly-a1 --output json 2>"$ERR"); res="$res $(head -c 3000 "$ERR")"
  if echo "$res" | grep -q '"lifecycle-state"'; then
    id=$(echo "$res" | grep -o 'ocid1\.instance\.[A-Za-z0-9._-]*' | head -1)
    echo "A1 VM BAN GAYI: $id"
    sleep ${A1_WAIT:-90}
    ip=$(J compute instance list-vnics --instance-id "$id" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data',[]); print(d[0].get('public-ip','') if d else '')")
    { echo "### Oracle A1 VM ban gayi"; echo "Public IP: **$ip** (setup 5-10 min mein khud poora hoga; phir http://$ip:8000)"; } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
    echo "::notice::A1 ready: http://$ip:8000 (5-10 min baad)"
    exit 0
  fi
  if echo "$res" | grep -qiE "out of (host )?capacity|OutOfHostCapacity|TooManyRequests|InternalError"; then
    echo "  capacity nahi: $(echo "$res" | grep -oiE 'out of[^"]*capacity[^"]*|LimitExceeded|TooManyRequests' | head -1)"; continue
  fi
  if echo "$res" | grep -q "LimitExceeded"; then
    LIMIT_MSG=$(echo "$res" | tr '\n' ' ' | tr -s ' ' | grep -oE '"message": *"[^"]*"' | head -1); LIMIT_HIT=1; echo "  limit: ${OC} OCPU / ${MEM} GB nahi milti, chhoti try"; break
  fi
  echo "::error::launch fail: $(echo "$res" | tr '\n' ' ' | grep -oE '"(code|message)": *"[^"]*"' | head -4 | tr '\n' ' ') || $(echo "$res" | tr '\n' ' ' | tr -s ' ' | head -c 1400)"; exit 1
done
[ "$LIMIT_HIT" = 1 ] && continue
break
done
if [ "$LIMIT_HIT" = 1 ]; then
  echo "::error::Oracle account mein A1 ki limit nahi: $LIMIT_MSG (Console > Governance > Limits, Quotas and Usage > Compute: Standard.A1 cores dekho)"; exit 1
fi
echo "abhi capacity nahi - 10 minute baad dobara"
exit 0
