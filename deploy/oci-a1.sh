#!/usr/bin/env bash
# GitHub Action se har 10 minute: Oracle Always-Free A1 (4 core / 24 GB) banane ki koshish. "Out of capacity" aam hai -> chup-chaap
# agli baar. Ban jaye to dobara nahi banata. Env: OCI_USER OCI_TENANCY OCI_FINGERPRINT OCI_REGION OCI_KEY SSH_PUBKEY MESH_SECRET
set -uo pipefail
for v in OCI_USER OCI_TENANCY OCI_FINGERPRINT OCI_REGION OCI_KEY SSH_PUBKEY MESH_SECRET; do
  [ -n "${!v:-}" ] || { echo "::warning::secret $v nahi mila - A1 skip"; exit 0; }
done
mkdir -p ~/.oci && printf '%s\n' "$OCI_KEY" > ~/.oci/key.pem && chmod 600 ~/.oci/key.pem
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
J() { oci "$@" --output json 2>&1; }

out=$(J compute instance list --compartment-id "$T" --all) || { echo "::error::OCI login/list fail (key/fingerprint/region check karo): $out" | head -c 600; exit 1; }
if echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data',[]); sys.exit(0 if any(i['shape']=='VM.Standard.A1.Flex' and i['lifecycle-state'] not in ('TERMINATED','TERMINATING') for i in d) else 1)"; then
  echo "A1 VM pehle se maujood hai - kuch nahi karna"; exit 0
fi

subnet=$(J network subnet list --compartment-id "$T" --all | python3 -c "
import sys,json
d=json.load(sys.stdin).get('data',[])
d=[s for s in d if not s.get('prohibit-public-ip-on-vnic') and s['lifecycle-state']=='AVAILABLE']
print(d[0]['id'] if d else '')")
[ -n "$subnet" ] || { echo "::error::public subnet nahi mili (VCN banao ya pehli VM wali subnet chahiye)"; exit 1; }
image=$(J compute image list --compartment-id "$T" --operating-system "Canonical Ubuntu" --operating-system-version "22.04" \
  --shape VM.Standard.A1.Flex --sort-by TIMECREATED --sort-order DESC --limit 1 | python3 -c "
import sys,json; d=json.load(sys.stdin).get('data',[]); print(d[0]['id'] if d else '')")
[ -n "$image" ] || { echo "::error::Ubuntu 22.04 ARM image nahi mili"; exit 1; }

printf '%s\n' "$SSH_PUBKEY" > /tmp/key.pub
sed "s|__MESH_SECRET__|$MESH_SECRET|" deploy/a1-cloud-init.sh > /tmp/init.sh

for ad in $(J iam availability-domain list --compartment-id "$T" | python3 -c "import sys,json; print(' '.join(a['name'] for a in json.load(sys.stdin)['data']))"); do
  echo "try AD $ad"
  res=$(oci compute instance launch --availability-domain "$ad" --compartment-id "$T" --shape VM.Standard.A1.Flex \
    --shape-config '{"ocpus":4,"memoryInGBs":24}' --subnet-id "$subnet" --image-id "$image" --assign-public-ip true \
    --boot-volume-size-in-gbs 100 --ssh-authorized-keys-file /tmp/key.pub --user-data-file /tmp/init.sh \
    --display-name talashly-a1 --output json 2>&1)
  if echo "$res" | grep -q '"lifecycle-state"'; then
    id=$(echo "$res" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
    echo "A1 VM BAN GAYI: $id"
    sleep ${A1_WAIT:-90}
    ip=$(J compute instance list-vnics --instance-id "$id" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data',[]); print(d[0].get('public-ip','') if d else '')")
    { echo "### Oracle A1 VM ban gayi"; echo "Public IP: **$ip** (setup 5-10 min mein khud poora hoga; phir http://$ip:8000)"; } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
    echo "::notice::A1 ready: http://$ip:8000 (5-10 min baad)"
    exit 0
  fi
  if echo "$res" | grep -qiE "out of (host )?capacity|OutOfHostCapacity|LimitExceeded|TooManyRequests|InternalError"; then
    echo "  capacity nahi: $(echo "$res" | grep -oiE 'out of[^"]*capacity[^"]*|LimitExceeded|TooManyRequests' | head -1)"; continue
  fi
  echo "::error::launch fail: $(echo "$res" | tail -c 500)"; exit 1
done
echo "abhi capacity nahi - 10 minute baad dobara"
exit 0
