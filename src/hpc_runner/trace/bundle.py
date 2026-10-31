"""``hpc trace bundle``: scrub a trace and pack it for an issue report (D-TR8).

The local trace is raw. Before it leaves the machine the bundle replaces,
with stable tokens, the values found in the places the scheduler puts them:

* **users** (``user1``, ...): qacct ``owner``/``group``, ``JB_owner``/
  ``JB_group``, qdel's ``<user> has registered``, argv after ``-u``/``-o``,
  the ``/home/<user>`` path component, the current user. Replaced
  everywhere in every file, since a name recurs in paths and messages.
* **hosts** (``host1``, ...): qacct ``hostname``, the ``@host`` of a queue
  instance, ``on host "..."`` in commlib errors, ``-l hostname=``, this
  machine's name. Replaced everywhere.
* **names** (``name1``, ...): job names, projects, departments, accounts
  and queues, replaced only where they are written as such (qacct fields,
  ``JB_job_name``, ``JB_name``, qsub's ``("name")``, argv after ``-N``,
  qstat's name column), because a job may be called ``array`` or ``ok``
  and a global replacement would rewrite the scheduler's own words.
* **environment**: every ``VA_value`` of a ``qstat -j`` job's environment
  list becomes ``[scrubbed]``.

The map is printed to stderr so the user can check it before attaching
the bundle. Free text is not searched: a job's own stdout is never in a
trace, only the scheduler's.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hpc_runner.core.host import current_user
from hpc_runner.trace.compare import Record, load_records

#: Values that look like names but are the scheduler's own placeholders.
STOPLIST = frozenset({"NONE", "undefined", "defaultdepartment", "sge", "*", "n/a", "unknown"})

# (pattern, category): the value is group "v". `name` patterns are also the
# only places names are replaced; `user`/`host` patterns only collect.
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:owner|group)\s+(?P<v>\S+)", re.M), "user"),
    (re.compile(r"^hostname\s+(?P<v>\S+)", re.M), "host"),
    (re.compile(r"^(?:jobname|project|department|account|qname)\s+(?P<v>\S+)", re.M), "name"),
    (re.compile(r"<(?:JB_owner|JB_group)>(?P<v>[^<]+)<"), "user"),
    (
        re.compile(
            r"<(?:JB_job_name|JB_name|JB_project|JB_department|JB_account|QR_name)>(?P<v>[^<]+)<"
        ),
        "name",
    ),
    (re.compile(r"<queue_name>(?P<v>[^@<]+)@"), "name"),
    (re.compile(r"<queue_name>[^@<]+@(?P<v>[^<]+)<"), "host"),
    (re.compile(r"^(?P<v>\S+) has registered the job", re.M), "user"),
    (re.compile(r'on host "(?P<v>[^"]+)"'), "host"),
    (re.compile(r'Your job(?:-array)? [\d.:-]+ \("(?P<v>[^"]+)"\)'), "name"),
    # plain qstat: job-ID prior name user ...
    (re.compile(r"^\s*\d+\s+[\d.]+\s+(?P<v>\S+)\s+\S+\s+[A-Za-z]+\s", re.M), "name"),
    (re.compile(r"^\s*\d+\s+[\d.]+\s+\S+\s+(?P<v>\S+)\s+[A-Za-z]+\s", re.M), "user"),
    (re.compile(r"/home/(?P<v>[A-Za-z0-9_.-]+)"), "user"),
]
_ENV_VALUE = re.compile(r"(<VA_value>)[^<]*(</VA_value>)")
_ARGV_USER_FLAGS = {"-u", "-o"}
_ARGV_NAME_FLAGS = {"-N"}


@dataclass
class Scrubber:
    """Collects names from records, then replaces them."""

    extra: Iterable[str] = ()
    tokens: dict[str, dict[str, str]] = field(
        default_factory=lambda: {"user": {}, "host": {}, "name": {}}
    )

    def __post_init__(self) -> None:
        self.add("user", current_user())
        self.add("host", socket.gethostname())
        self.add("host", socket.gethostname().split(".")[0])
        home = os.environ.get("HOME", "")
        if home.startswith("/home/"):
            self.add("user", home.split("/")[2])
        for value in self.extra:
            self.add("user", value)

    def add(self, category: str, value: str) -> str | None:
        """Register *value* under *category*; return its token (None when refused)."""
        value = value.strip()
        if len(value) < 2 or value in STOPLIST or not re.fullmatch(r"[A-Za-z0-9_.@-]+", value):
            return None
        table = self.tokens[category]
        if value not in table:
            table[value] = f"{category}{len(table) + 1}"
        return table[value]

    # -- collection -----------------------------------------------------------

    def collect(self, records: Iterable[Record]) -> None:
        for record in records:
            self._collect_argv(record.argv)
            for text in (record.stdout, record.stderr):
                if text:
                    self._collect_text(text)

    def _collect_argv(self, argv: list[str]) -> None:
        for i, token in enumerate(argv[:-1]):
            value = argv[i + 1]
            if token in _ARGV_USER_FLAGS and argv[0] in ("qstat", "qacct"):
                self.add("user", value)
            elif token in _ARGV_NAME_FLAGS:
                self.add("name", value)
            elif token == "-l" and value.startswith("hostname="):
                self.add("host", value.split("=", 1)[1])

    def _collect_text(self, text: str) -> None:
        for pattern, category in _RULES:
            for match in pattern.finditer(text):
                self.add(category, match.group("v"))

    # -- replacement ----------------------------------------------------------

    def text(self, text: str) -> str:
        """Scrub one text: names where written as such, users and hosts everywhere."""
        names = self.tokens["name"]
        if names:

            def positional(match: re.Match[str]) -> str:
                value = match.group("v")
                if value not in names:
                    return match.group(0)
                start, end = match.span("v")
                return (
                    match.group(0)[: start - match.start()]
                    + names[value]
                    + match.group(0)[end - match.start() :]
                )

            for pattern, category in _RULES:
                if category == "name":
                    text = pattern.sub(positional, text)
        text = _ENV_VALUE.sub(r"\1[scrubbed]\2", text)
        for category in ("user", "host"):
            for value, token in sorted(self.tokens[category].items(), key=lambda kv: -len(kv[0])):
                text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", token, text)
        return text

    def argv(self, argv: list[str]) -> list[str]:
        out = list(argv)
        for i, token in enumerate(out[:-1]):
            if token in _ARGV_NAME_FLAGS and out[i + 1] in self.tokens["name"]:
                out[i + 1] = self.tokens["name"][out[i + 1]]
        return [self.text(a) for a in out]

    def record(self, data: dict[str, Any]) -> dict[str, Any]:
        """A scrubbed copy of one record's JSON."""
        out = dict(data)
        if isinstance(out.get("argv"), list):
            out["argv"] = self.argv([str(a) for a in out["argv"]])
        for key in ("stdout", "stderr", "error", "cwd"):
            if isinstance(out.get(key), str):
                out[key] = self.text(out[key])
        return out

    def summary(self) -> list[tuple[str, str, str]]:
        """``(category, original, token)`` for every replacement, in token order."""
        rows: list[tuple[str, str, str]] = []
        for category, table in self.tokens.items():
            rows.extend((category, value, token) for value, token in table.items())
        return sorted(rows, key=lambda r: (r[0], int(re.sub(r"\D", "", r[2]) or 0)))


def scheduler_banner(timeout: float = 5.0) -> str | None:
    """The first line of ``qstat -help`` (the SGE version), or None."""
    try:
        proc = subprocess.run(
            ["qstat", "-help"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = proc.stdout or proc.stderr
    return text.splitlines()[0].strip() if text.strip() else None


def bundle(
    root: Path,
    out: Path,
    *,
    raw: bool = False,
    extra: Iterable[str] = (),
    scheduler_config: Mapping[str, Any] | None = None,
    log: Any = None,
) -> tuple[Path, Scrubber | None]:
    """Write *out* (a ``.tar.gz``) from the trace under *root*; return it and the scrubber used.

    With *raw* nothing is replaced and the scrubber is None.
    """
    from hpc_runner import __version__

    records = load_records(root)
    scrubber: Scrubber | None = None
    if not raw:
        scrubber = Scrubber(extra=extra)
        scrubber.collect(records)

    def clean(text: str) -> str:
        return scrubber.text(text) if scrubber else text

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = f"hpc-trace-{stamp}"
    meta: dict[str, Any] = {
        "hpc_runner": str(__version__),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "scheduler": scheduler_banner(),
        "records": len(records),
        "days": sorted({r.path.parent.name for r in records}),
        "scrubbed": not raw,
        "replacements": ({c: len(t) for c, t in scrubber.tokens.items()} if scrubber else None),
    }
    if scheduler_config is not None:
        meta["schedulers"] = json.loads(clean(json.dumps(scheduler_config, default=str)))

    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        _add(tar, f"{prefix}/meta.json", json.dumps(meta, indent=2) + "\n")
        for record in records:
            data = json.loads(record.path.read_text())
            if scrubber:
                data = scrubber.record(data)
            rel = record.path.relative_to(root)
            _add(tar, f"{prefix}/trace/{rel}", json.dumps(data, indent=2) + "\n")
    if log is not None:
        log(f"{len(records)} records -> {out}")
    return out, scrubber


def _add(tar: tarfile.TarFile, name: str, text: str) -> None:
    payload = text.encode()
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = int(datetime.now().timestamp())
    tar.addfile(info, io.BytesIO(payload))


def print_summary(scrubber: Scrubber, stream: Any = None) -> None:
    """Print the replacement map to *stream* (stderr by default, looked up at call time)."""
    stream = stream if stream is not None else sys.stderr
    rows = scrubber.summary()
    if not rows:
        print("scrubbed nothing: no names found", file=stream)
        return
    print("scrubbed (check before attaching):", file=stream)
    for category, value, token in rows:
        print(f"  {category:<5} {value} -> {token}", file=stream)
