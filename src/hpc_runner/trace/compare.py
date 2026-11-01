"""``hpc trace compare``: run a trace back through the parsers (D-TR9..D-TR11).

A trace is a directory of JSON records written by :mod:`.recorder` (or the
JSON fixtures under ``tests/unit/fixtures/``). Each record's argv is reduced
to a *shape* (ids, paths, names replaced by ``<arg>``) and the shape picks
the parser that consumes that output in the scheduler. A finding is a record
the parser could not make sense of: an exception, nothing parsed from text
that plainly holds something, a ``JobInfo`` with ``UNKNOWN`` status, or a
field the record has that the ``JobInfo`` lacks. With a fixture root, the
shapes the trace holds that no captured fixture covers are listed too.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hpc_runner.core.result import JobStatus

_ID = re.compile(r"^\d+(\.\d+)?$")


@dataclass
class Record:
    """One scheduler call as recorded."""

    path: Path
    argv: list[str]
    returncode: int | None
    stdout: str | None
    stderr: str | None
    error: str | None = None

    @property
    def shape(self) -> str:
        return shape(self.argv)

    @classmethod
    def load(cls, path: Path) -> Record | None:
        """The record in *path*, or None when the file is not one."""
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("argv"), list):
            return None
        return cls(
            path=path,
            argv=[str(a) for a in data["argv"]],
            returncode=data.get("returncode"),
            stdout=data.get("stdout"),
            stderr=data.get("stderr"),
            error=data.get("error"),
        )


@dataclass
class Finding:
    record: Record
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": str(self.record.path),
            "shape": self.record.shape,
            "message": self.message,
        }


@dataclass
class Report:
    records: list[Record]
    findings: list[Finding]
    #: shape -> number of records
    shapes: Counter[str] = field(default_factory=Counter)
    #: shapes a fixture root covers (None when no root was given)
    covered: set[str] | None = None

    @property
    def uncovered(self) -> list[str]:
        if self.covered is None:
            return []
        return sorted(s for s in self.shapes if s not in self.covered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": len(self.records),
            "shapes": [
                {
                    "shape": s,
                    "calls": n,
                    "findings": sum(1 for f in self.findings if f.record.shape == s),
                    "fixture": None if self.covered is None else s in self.covered,
                }
                for s, n in sorted(self.shapes.items())
            ],
            "findings": [f.as_dict() for f in self.findings],
            "uncovered": self.uncovered,
        }


def shape(argv: Iterable[str]) -> str:
    """Reduce an argv to its shape: flags kept, values ``<arg>``, after ``--`` ``...``.

    ``qacct -j 1203`` and ``qacct -j 1204`` are one shape; ``qacct -j -t 2``
    is another because ``-t`` is a flag. ``-xml`` is a flag like any other.
    """
    out: list[str] = []
    for i, token in enumerate(argv):
        if i == 0:
            out.append(Path(token).name)
        elif token == "--":
            out.append("--")
            out.append("...")
            break
        elif token.startswith("-") and not _ID.match(token.lstrip("-")):
            out.append(token)
        else:
            out.append("<arg>")
    return " ".join(out)


def load_records(root: Path) -> list[Record]:
    """Every record under *root*, sorted by path (which sorts by time)."""
    records = []
    for path in sorted(root.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        record = Record.load(path)
        if record is not None:
            records.append(record)
    return records


def fixture_shapes(root: Path) -> set[str]:
    """Shapes of the argv recorded in a fixture root's ``manifest.json``.

    The capture writes ``files.<name>.argv``; JSON fixtures also carry their
    own ``argv`` and are read directly, so an older manifest still covers
    them.
    """
    covered: set[str] = set()
    manifest = root / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        for entry in data.get("files", {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("argv"), list):
                covered.add(shape(entry["argv"]))
    for record in load_records(root):
        covered.add(record.shape)
    return covered


# ---------------------------------------------------------------------------
# Checks: SGE
# ---------------------------------------------------------------------------

Check = Callable[[Record], list[str]]


def _check_qsub(rec: Record) -> list[str]:
    from hpc_runner.schedulers.sge.parser import parse_qsub_output

    if rec.returncode != 0 or rec.stdout is None:
        return []
    if parse_qsub_output(rec.stdout) is None:
        return ["qsub succeeded but no job id was parsed from its stdout"]
    return []


def _check_qstat(rec: Record) -> list[str]:
    from hpc_runner.schedulers.sge import parser

    if rec.stdout is None:
        return []
    if "-j" in rec.argv:
        if rec.returncode != 0 or "unknown_jobs" in rec.stdout:
            return []  # a finished or unknown job: the scheduler falls back to qacct
        if parser.qstat_unreachable(rec.stderr or ""):
            return []  # exits 0 with a comunication_error document (qstat_j_master_down)
        if not parser.parse_qstat_j_xml(rec.stdout):
            return ["qstat -j -xml held a job but no details were parsed"]
        return []
    if rec.returncode != 0:
        if parser.qstat_unreachable(rec.stderr or ""):
            return []
        return ["qstat failed and its stderr is not recognised as an unreachable qmaster"]
    if "-xml" in rec.argv:
        jobs = parser.parse_qstat_xml(rec.stdout)
        if not jobs and "<job_list" in rec.stdout:
            return ["qstat -xml lists jobs but none were parsed"]
    else:
        jobs = parser.parse_qstat_plain(rec.stdout)
        if not jobs and len(rec.stdout.splitlines()) > 2:
            return ["qstat lists jobs but none were parsed"]
    problems = []
    for job_id, info in jobs.items():
        state = info.get("state")
        if state is None:
            problems.append(f"job {job_id}: no state parsed")
        elif parser.state_to_status(str(state)) is JobStatus.UNKNOWN:
            problems.append(f"job {job_id}: state {state!r} maps to UNKNOWN")
    return problems


def _check_qacct(rec: Record) -> list[str]:
    from hpc_runner.schedulers.sge import parser

    if rec.stdout is None:
        return []
    if rec.returncode != 0:
        err = rec.stderr or ""
        if "not found" in err or "no matching" in err.lower():
            return []
        return ["qacct failed and its stderr is not a recognised 'not found'"]
    records = parser.parse_qacct_records(rec.stdout)
    if not records:
        if "jobnumber" in rec.stdout:
            return ["qacct printed records but none were parsed"]
        return []
    problems = []
    for raw in records:
        info = parser.qacct_to_job_info(raw)
        who = f"job {info.job_id or '?'}"
        if not info.job_id:
            problems.append(f"{who}: no jobnumber parsed")
        rerun = parser._leading_int(raw.get("failed")) in (24, 25)  # not the job's end
        if info.status is JobStatus.UNKNOWN and not rerun:
            exit_status, failed = raw.get("exit_status"), raw.get("failed")
            problems.append(
                f"{who}: status UNKNOWN (exit_status {exit_status!r}, failed {failed!r})"
            )
        if "exit_status" in raw and info.exit_code is None:
            problems.append(f"{who}: exit_status {raw['exit_status']!r} not parsed")
        if _has_value(raw.get("ru_wallclock")) and info.runtime is None:
            problems.append(f"{who}: ru_wallclock {raw['ru_wallclock']!r} not parsed")
        failed = parser.parse_failed(raw.get("failed"))
        if failed and failed[0] and not info.failed_reason:
            problems.append(f"{who}: failed {raw['failed']!r} has no reason text")
        for key, attr in (
            ("qsub_time", "submit_time"),
            ("start_time", "start_time"),
            ("end_time", "end_time"),
        ):
            if _has_value(raw.get(key)) and getattr(info, attr) is None:
                problems.append(f"{who}: {key} {raw[key]!r} not parsed")
    return problems


def _has_value(value: object) -> bool:
    """A qacct field that holds something to parse (not ``-/-``, ``NONE``, ``n/a``)."""
    return isinstance(value, str) and any(ch.isdigit() for ch in value)


#: argv[0] -> check (D-TR9). Another scheduler adds its commands here.
CHECKS: dict[str, Check] = {
    "qsub": _check_qsub,
    "qstat": _check_qstat,
    "qacct": _check_qacct,
}


def check(record: Record) -> list[str]:
    """What the parsers could not make of *record* (empty when all is well)."""
    if record.error:
        return [f"call did not complete: {record.error}"]
    checker = CHECKS.get(Path(record.argv[0]).name if record.argv else "")
    if checker is None:
        return []
    try:
        return checker(record)
    except Exception as exc:  # a parser raising is the finding
        return [f"parser raised {type(exc).__name__}: {exc}"]


def compare(root: Path, fixtures: Path | None = None) -> Report:
    """Replay every record under *root*; with *fixtures*, mark uncovered shapes."""
    records = load_records(root)
    findings = [Finding(rec, msg) for rec in records for msg in check(rec)]
    report = Report(records=records, findings=findings, shapes=Counter(r.shape for r in records))
    if fixtures is not None:
        report.covered = fixture_shapes(fixtures)
    return report
