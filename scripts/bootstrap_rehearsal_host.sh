#!/usr/bin/env bash
set -euo pipefail

expected_ip="${1:-}"
if [[ "$expected_ip" != "5.78.239.33" ]]; then
  printf 'Unexpected rehearsal address: %s\n' "$expected_ip" >&2
  exit 1
fi

if ! ip -4 -o address show scope global | awk '{print $4}' | grep -Fxq "$expected_ip/32"; then
  printf 'Expected address %s/32 was not found on the restored server. Global IPv4 addresses:\n' "$expected_ip" >&2
  ip -4 -o address show scope global | awk '{print $4}' >&2
  exit 1
fi

# Reaching this script already proves that sshd accepted the dedicated private
# key. Hetzner/cloud-init may omit or rewrite the public key's comment, so the
# comment text is not a reliable identity check.
printf 'PATT_REHEARSAL_SSH_KEY_ACCEPTED\n'

docker ps -q | xargs -r docker stop
systemctl stop docker.service docker.socket containerd.service || true
systemctl disable docker.service docker.socket
test "$(systemctl is-active docker.service || true)" = "inactive"

hostnamectl set-hostname patt-prod-rehearsal
touch /etc/patt-rehearsal
test -f /etc/patt-rehearsal

rm -f /etc/ssh/ssh_host_*
ssh-keygen -A
systemctl restart ssh.service
test "$(systemctl is-active ssh.service)" = "active"

printf 'PATT_REHEARSAL_DOCKER=%s\n' "$(systemctl is-active docker.service || true)"
printf 'PATT_REHEARSAL_HOSTNAME=%s\n' "$(hostnamectl --static)"
printf 'PATT_REHEARSAL_SSH_FINGERPRINT='
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
printf 'PATT_REHEARSAL_KNOWN_HOSTS=%s ' "$expected_ip"
cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub
printf 'PATT_REHEARSAL_BOOTSTRAP_COMPLETE\n'
