#!/bin/bash
# Fixed test users for every testbed image: alice 1001, bob 1002, tester 1003
# (uid == gid). No host UID is involved anywhere (TESTBED_PLAN.md section 4).
# tester has passwordless sudo: the fixture uses `sudo -u alice ...` and
# `sudo docker ...` (TESTBED_REVIEW.md D-T9).
set -euo pipefail

for spec in alice:1001 bob:1002 tester:1003; do
    user=${spec%%:*}
    id=${spec##*:}
    getent group "$user" >/dev/null || groupadd -g "$id" "$user"
    id -u "$user" >/dev/null 2>&1 || useradd -m -u "$id" -g "$id" -s /bin/bash "$user"
done

install -d -m 0750 /etc/sudoers.d
echo "tester ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/tester
chmod 0440 /etc/sudoers.d/tester
