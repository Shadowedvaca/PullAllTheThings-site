#!/usr/bin/env bash
set -euo pipefail

expected_ip="${1:-}"
test "$expected_ip" = "5.78.239.33"
ip -4 -o address show scope global | grep -Fq " $expected_ip/"
grep -Fq "patt-rehearsal-github-actions" /root/.ssh/authorized_keys

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
