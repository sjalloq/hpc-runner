"""Tests for SGE qacct multi-record parsing."""

from datetime import datetime, timedelta

from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.sge.parser import parse_qacct_records, qacct_to_job_info


class TestParseQacctRecords:
    """Tests for parse_qacct_records()."""

    SINGLE_RECORD = """\
==============================================================
qname        all.q
hostname     node1
owner        alice
jobname      sim_run
jobnumber    10001
exit_status  0
slots        4
ru_wallclock 123.456
qsub_time   Mon Feb 23 10:00:00 2026
start_time   Mon Feb 23 10:00:05 2026
end_time     Mon Feb 23 10:02:08 2026
"""

    TWO_RECORDS = """\
==============================================================
qname        all.q
hostname     node1
owner        alice
jobname      job_a
jobnumber    10001
exit_status  0
slots        2
ru_wallclock 60.0
qsub_time   Mon Feb 23 10:00:00 2026
start_time   Mon Feb 23 10:00:05 2026
end_time     Mon Feb 23 10:01:05 2026
==============================================================
qname        gpu.q
hostname     node2
owner        alice
jobname      job_b
jobnumber    10002
exit_status  1
slots        8
ru_wallclock 300.0
qsub_time   Mon Feb 23 11:00:00 2026
start_time   Mon Feb 23 11:00:10 2026
end_time     Mon Feb 23 11:05:10 2026
"""

    def test_single_record(self):
        records = parse_qacct_records(self.SINGLE_RECORD)
        assert len(records) == 1
        assert records[0]["jobnumber"] == "10001"
        assert records[0]["jobname"] == "sim_run"
        assert records[0]["exit_status"] == "0"

    def test_two_records(self):
        records = parse_qacct_records(self.TWO_RECORDS)
        assert len(records) == 2
        assert records[0]["jobnumber"] == "10001"
        assert records[1]["jobnumber"] == "10002"

    def test_empty_input(self):
        records = parse_qacct_records("")
        assert records == []

    def test_separator_only(self):
        records = parse_qacct_records("=" * 62)
        assert records == []


class TestQacctToJobInfo:
    """Tests for qacct_to_job_info()."""

    def test_successful_job(self):
        record = {
            "jobnumber": "10001",
            "jobname": "sim_run",
            "owner": "alice",
            "exit_status": "0",
            "qname": "all.q",
            "hostname": "node1",
            "slots": "4",
            "ru_wallclock": "123.456",
            "qsub_time": "Mon Feb 23 10:00:00 2026",
            "start_time": "Mon Feb 23 10:00:05 2026",
            "end_time": "Mon Feb 23 10:02:08 2026",
        }
        info = qacct_to_job_info(record)

        assert info.job_id == "10001"
        assert info.name == "sim_run"
        assert info.user == "alice"
        assert info.status == JobStatus.COMPLETED
        assert info.exit_code == 0
        assert info.queue == "all.q"
        assert info.node == "node1"
        assert info.cpu == 4
        assert info.runtime == timedelta(seconds=123.456)
        assert info.submit_time == datetime(2026, 2, 23, 10, 0, 0)
        assert info.start_time == datetime(2026, 2, 23, 10, 0, 5)
        assert info.end_time == datetime(2026, 2, 23, 10, 2, 8)

    def test_failed_job(self):
        record = {
            "jobnumber": "10002",
            "jobname": "bad_run",
            "owner": "bob",
            "exit_status": "137",
            "qname": "gpu.q",
            "hostname": "node2",
            "slots": "1",
            "ru_wallclock": "5.0",
        }
        info = qacct_to_job_info(record)

        assert info.status == JobStatus.FAILED
        assert info.exit_code == 137

    def test_missing_optional_fields(self):
        record = {
            "jobnumber": "10003",
            "jobname": "minimal",
            "owner": "charlie",
            "exit_status": "0",
        }
        info = qacct_to_job_info(record)

        assert info.job_id == "10003"
        assert info.status == JobStatus.COMPLETED
        assert info.queue is None
        assert info.node is None
        assert info.cpu is None
        assert info.runtime is None
        assert info.submit_time is None

    def test_timestamps_parsed(self):
        record = {
            "jobnumber": "10004",
            "jobname": "timed",
            "owner": "dave",
            "exit_status": "0",
            "qsub_time": "Sun Feb 22 23:59:59 2026",
            "start_time": "Mon Feb 23 00:00:01 2026",
            "end_time": "Mon Feb 23 01:30:00 2026",
        }
        info = qacct_to_job_info(record)

        assert info.submit_time == datetime(2026, 2, 22, 23, 59, 59)
        assert info.start_time == datetime(2026, 2, 23, 0, 0, 1)
        assert info.end_time == datetime(2026, 2, 23, 1, 30, 0)

    def test_empty_record(self):
        info = qacct_to_job_info({})
        assert info.job_id == ""
        assert info.name == ""
        assert info.status == JobStatus.UNKNOWN


class TestParseQstatJXml:
    """qstat -j -xml parsing for get_job_details (moved from the scheduler)."""

    XML = """<?xml version='1.0'?>
<detailed_job_info xmlns:xsd="http://arc.liv.ac.uk/repos/darcs/sge/source/dist/util/resources/schemas/qstat/detailed_job_info.xsd">
  <djob_info>
    <element>
      <JB_job_number>42</JB_job_number>
      <JB_job_name>sim</JB_job_name>
      <JB_owner>alice</JB_owner>
      <JB_cwd>/work</JB_cwd>
      <JB_stdout_path_list><path_list><PN_path>sim.out</PN_path></path_list></JB_stdout_path_list>
      <JB_merge_stderr>true</JB_merge_stderr>
      <JB_hard_resource_list>
        <qstat_l_requests><CE_name>gpu</CE_name><CE_stringval>2</CE_stringval></qstat_l_requests>
      </JB_hard_resource_list>
      <JB_pe>smp</JB_pe>
      <JB_pe_range><ranges><RN_min>4</RN_min><RN_max>4</RN_max></ranges></JB_pe_range>
      <JB_jid_predecessor_list><job_predecessors><JRE_job_number>41</JRE_job_number></job_predecessors></JB_jid_predecessor_list>
    </element>
  </djob_info>
</detailed_job_info>
"""

    def test_fields(self):
        from pathlib import Path

        from hpc_runner.schedulers.sge.parser import parse_qstat_j_xml

        data = parse_qstat_j_xml("noise before\n" + self.XML)
        assert data["name"] == "sim"
        assert data["user"] == "alice"
        assert data["stdout_path"] == Path("/work/sim.out")
        assert data["stderr_path"] == Path("/work/sim.out")  # merged
        assert data["resources"] == {"gpu": "2"}
        assert (data["pe_name"], data["pe_range"]) == ("smp", "4")
        assert data["dependencies"] == ["41"]

    def test_garbage_is_empty(self):
        from hpc_runner.schedulers.sge.parser import parse_qstat_j_xml

        assert parse_qstat_j_xml("not xml") == {}

    def test_task_job_id(self, monkeypatch, tmp_path):
        from hpc_runner.core.config import HPCConfig
        from hpc_runner.schedulers.sge import SGEScheduler

        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        assert SGEScheduler(HPCConfig()).task_job_id("42", 7) == "42.7"
