"""exp11: Ctrl-C at a terminal during an interactive `hpc run` (SGE soge).

Run inside login as tester:
  docker exec -u tester -w /work hpc-testbed-sge-soge-login \
      /opt/venv/3.12/bin/python /src/docs/dev/slurm/runs/sge-soge-2026-11-17-exp11.py
The command runs on a pty, as from a shell; Ctrl-C is the byte 0x03 typed at it.
"""

import os
import pty
import select
import subprocess
import sys
import time

argv = sys.argv[1:] or ["/opt/venv/3.12/bin/hpc", "run", "sleep", "300"]
print("command:", " ".join(argv))
pid, fd = pty.fork()
if pid == 0:
    os.execv(argv[0], argv)

out = b""


def drain(seconds: float) -> None:
    global out
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if select.select([fd], [], [], 0.2)[0]:
            try:
                out += os.read(fd, 4096)
            except OSError:
                return


def running() -> str:
    text = subprocess.run(["qstat", "-u", "tester"], capture_output=True, text=True).stdout
    return " ".join(line.split()[0] for line in text.splitlines()[2:] if " r " in line)


for _ in range(60):
    drain(1)
    if running():
        break
print("running job:", running())
os.write(fd, b"\x03")
print("typed Ctrl-C")
drain(15)
_, status = os.waitpid(pid, 0)
print("exit code:", os.waitstatus_to_exitcode(status))
print("--- terminal output ---")
print(out.decode(errors="replace"))
print("--- qstat 5 s later ---")
time.sleep(5)
print(subprocess.run(["qstat", "-u", "tester"], capture_output=True, text=True).stdout or "(empty)")
