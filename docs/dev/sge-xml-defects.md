# SGE XML output defects

Research notes, 2026-11-01. They cover defects in the XML that gridengine
8.1.9 (the testbed's Debian trixie build, `testbed/sge/soge/`) writes for
`qstat -xml` and `qstat -j -xml`. The question was whether the `<job ids>`
tag found under D-T46 (`docs/dev/testbed/TESTBED_REVIEW.md`) is a known bug,
and what else of the same kind is still live.

Short answer: no public report of `<job ids>` was found. It is one of four
XML defects still present in 8.1.9. Open Cluster Scheduler (OCS), the
maintained fork, has fixed one of them.

Source references are to the Debian orig tarball
`gridengine_8.1.9+dfsg.orig.tar.gz` (paths under `sge-8.1.9/source/`) unless
marked OCS, which means `hpc-gridware/clusterscheduler` master as of
2026-11-01.

## Summary

| # | Defect | Effect | Testbed | OCS master |
|---|--------|--------|---------|------------|
| 1 | List name `"job ids"` becomes an XML tag | `qstat -j -xml` not well-formed when a job has some scheduler messages | captured (`qstat_j_requeue_pending.xml`) | still present |
| 2 | `<` escaped as `%lt;` | well-formed, but `<` in any string value is corrupted | confirmed | fixed (`ocs_EscapedString.h`) |
| 3 | Control characters not filtered | any C0 control char (other than tab, LF, CR) in a string makes the document unparseable | confirmed (env value `\x01`) | still present |
| 4 | Root element `comunication_error` (sic) | master-down document has a misspelt root | captured (`qstat_j_master_down.json`) | not checked |

## 1. `<job ids>`

**Mechanism.** `qstat -j -xml` has no hand-written XML writer. It walks the
internal CULL lists and elements generically
(`libs/sgeobj/sge_cull_xml.c`, `lWriteListXML_` and `lWriteElemXML_`):

- an element field is written as `<FIELD_NAME>` (from `lNm2Str`), which is
  always a valid name;
- each element of a sublist is wrapped in the **list's own name**
  (`lGetListName(lp)`, `sge_cull_xml.c:241`). The name is not validated or
  escaped. An empty name means no wrapper at all (`tag_ok` is false), and
  the literal `"No list name specified"` becomes `<element>`.

So any CULL list whose internal name is not an XML name produces bad XML
once it reaches a `-xml` dump. Many internal lists have names with spaces
(`"job info messages"`, `"env list"`, `"Load Thresholds"`, ...), but most
never reach qstat output. The one that does is the scheduler message's job
number list.

**Where the name comes from.** `libs/sched/schedd_message.c` builds each
scheduler message (`MES_Type`) with a `MES_job_number_list`. The wrapper tag
for the job ids inside it depends on which path built the message:

| Path | List name | XML |
|------|-----------|-----|
| `schedd_mes_add()` (`:415`), `schedd_mes_add_join()` (`:504`): message added directly for one job | `"job ids"` | `<job ids>` (invalid) |
| `schedd_mes_set_tmp_list()` (`:626`): cached category messages replayed onto a job, via `lAddSubUlong` → `lAddElemUlong` (`libs/cull/cull_multitype.c:4474`) | `"ulong_sublist"` | `<ulong_sublist>` |
| `schedd_mes_find_others()`: first message of a category, via `schedd_mes_get_same_category_jids` → `lAddElemUlong` | `"ulong_sublist"` | `<ulong_sublist>` |
| `schedd_mes_find_others()` (`:96`): later messages in the same category, `lCopyList("", ...)` | `""` | no wrapper; `<ULNG_value>` directly inside `<MES_job_number_list>` |

The captures agree. The `Rq` job's queue/host mismatch messages (numbers
44, 17 and 6) use `<job ids>`. The hold message ("Job is in hold state",
`SCHEDD_INFO_JOBHOLD_`, added in `libs/sched/sge_job_schedd.c:980`) uses
`<ulong_sublist>`. Which path a given message takes depends on job category
and scheduler state, not on the message number. Any message can therefore
appear with any of the three shapes. Messages exist only with
`schedd_job_info true` (the testbed's setting); a site with it `false` never
sees this.

**OCS.** `source/libs/sched/schedd_message.cc` still calls
`lCreateList("job ids", ULNG_Type)` (lines 364 and 443). CS-1991 (commit
`974661a00`, 2026-05-03, "XML output of jobs might contain empty tags for
cull lists with an empty name") changed the empty-name case to write
`<element>`. Its rewrite had dropped the `tag_ok` check and was writing
`<>`. An OCS testbed (phase 5) will therefore show `<job ids>`,
`<ulong_sublist>` and `<element>` as the three wrappers.

**hpc-runner.** `_parse_xml_root` (`src/hpc_runner/schedulers/sge/parser.py`)
rewrote `<job ids>` and `</job ids>` to `job_ids` before parsing (D-T46);
since D-T47 it repairs any spaced tag name. The parser
never reads message contents, so the wrapper shape does not matter beyond
well-formedness. The D-T46 comment said the tag appears "whenever the job has a
scheduling message". That was too broad: it appears only for messages built
by the direct-add path above. The D-T47 comment was rewritten.

## 2. `<` escaped as `%lt;`

**Mechanism.** `escape_string()` (`libs/sgeobj/sge_cull_xml.c:655`) is the
escaper for every string and host value in the generic dump, and for
attribute values. Its first case is a typo:

```c
case '<' : sge_dstring_append(target, "%lt;");
```

`>`, `&`, `'` and `"` are escaped correctly. The output stays well-formed,
so nothing fails to parse. A literal `<` simply comes back as the four
characters `%lt;`.

**Testbed evidence** (2026-11-01, job submitted as tester, then deleted):

```
qsub -h -N 'a<b' ...           ->  <JB_name>a%lt;b</JB_name>          (qstat -xml)
                                   <JB_job_name>a%lt;b</JB_job_name>  (qstat -j -xml)
-v 'PROBE=a<b&c"d'             ->  <VA_value>a%lt;b&amp;c&quot;d</VA_value>
/bin/echo 'a<b' 'x&y' 'q"z'    ->  <ST_name>a%lt;b</ST_name> <ST_name>x&amp;y</ST_name> <ST_name>q&quot;z</ST_name>
```

**hpc-runner.** The parser reads `JB_name`, `JB_job_name`, `JB_job_args`,
`JB_env_list`, `JB_cwd` and the `PN_path` of the stdout/stderr path lists.
Any of these containing `<` is silently wrong. A shell redirection in a job
argument is the likeliest real case.

**OCS.** Fixed. String output goes through `ocs::EscapedString`
(`source/libs/sgeobj/ocs_EscapedString.h`), which writes `&lt;`, and
`escape_string()` delegates to it. A client that has to read both 8.1.9 and
OCS cannot just undo `%lt;` everywhere, because `%lt;` in OCS output would
then be a literal string the user typed.

## 3. Control characters

**Mechanism.** `escape_string()` passes every character it does not
special-case through unchanged. XML 1.0 forbids C0 control characters
other than tab, LF and CR, even escaped, so one of them anywhere in a
string makes the whole document not well-formed.

**Testbed evidence.** A job submitted with `-v "CTRL=$(printf 'x\001y')"`
gives a `qstat -j -xml` document that Python's `ElementTree` rejects:
`not well-formed (invalid token): line 50, column 21`. Tab passes through
literally and is legal.

**Why this is the serious one for hpc-runner.** A job submitted with `-V`
(`inherit_env`) copies the whole submit-side environment into
`JB_env_list`. A user who exports one variable containing a raw control
character, for example a prompt or colour variable holding a literal ESC
(`\x1b`), makes `qstat -j -xml` unparseable for every job they submit.
`get_job_details` then gets nothing back, as it did for `<job ids>` before
D-T46. The `<job ids>` repair does not help here.

**OCS.** Still present. `EscapedString` has the same five cases and a
pass-through default.

## 4. `comunication_error`

When the qmaster is unreachable, `qstat -j -xml` exits 0 and writes a
document whose root is `<comunication_error>`
(`clients/qstat/qstat_xml.c:1143` and `:1202`). SGE NEWS for 8.1.3 says
"Correct spelling of "comunication_error" in qstat XML error output", but
these two call sites were missed. The source also notes the gap
(`sge_cull_xml.c`, `xml_getHead`):
`/* fixme: also called with "communication_error" and "unknown_jobs" */`.
No schema is attached to either root. hpc-runner already handles the
captured document (`qstat_j_master_down.json`; `trace compare` fix in
D-T46).

## Upstream trail

No report of `<job ids>`, `%lt;` or the control-character case was found
in the places below. The Son of Grid Engine tracker (`arc.liv.ac.uk/trac/SGE`)
is offline, so tickets #314 and #359 could not be read.

- **Sun / Oracle N1 Grid Engine 6.0**, patch 123038-01 (2006): 5085392
  "qstat -j -xml generates no parseble xml output"; 6384812 "qstat produces
  non-well-formed XML output"; 6289455 "qstat -XML output does not match the
  schema"; 6278727 "qstat -xml -urg output contains badly formatted
  numbers"; 6255804 "job in error state breaks qstat -f -xml"; 6211243
  "qstat -ext -xml command is broken"; 5081821 "qstat XML output typo";
  6305095 "qstat schema files are incomplete".
- **Sun Grid Engine 6.2 Changelog** (in the tarball, `Changelog`):
  MP-2007-08-31-0 and MP-2007-09-25-0, `qstat -j` messages disagree between
  plain and XML output (Bugster 6440408, 6440412, Issue 2076);
  MP-2007-10-26-0, `qstat -j -xml` shows more than `qstat -j` (6353628);
  MP-2009-01-05-0, `qstat -j -xml` schema not well documented (6320659,
  Issue 1776); JG-2009-09-05-1, memory access problem in `qstat -xml`;
  RC-2007-06-22-0, `qhost -xml` lacks `>`; MP-2008-03-27-0, `qhost -xml`
  has the wrong namespace.
- **Son of Grid Engine NEWS** (`NEWS`): 8.0.0a "Fix some ill-formed output
  from qstat -xml [#314]"; 8.1.3 "Correct more bad XML [#314]", "Correct
  qstat -pri -xml output [#359]", "Use correct schema with qstat -xml", and
  the incomplete `comunication_error` fix; 8.1.6 "Fix detailed_job_info XML
  schema for -ncb case".
- **Debian** (`gridengine_8.1.9+dfsg-14.debian.tar.xz`): no XML patches in
  `debian/patches`. The changelog has one XML fix, "Fix
  cqueue_summary_xml_handler_init" (Debian bug #749413).
- **Open Cluster Scheduler**: CS-1991 (above). The `%lt;` typo is gone with
  the `EscapedString` rewrite; no changelog entry for it was found.
- **Third-party reports** of SGE XML that does not parse are generic. One
  is an Open OnDemand thread on SoGE 8.1.9 "body parsing error"; the
  maintainer asked whether the XML was valid, and no resolution is shown.
  Another is a StarCluster thread (2012) on `qhost -xml` from Open Grid
  Scheduler. Neither identifies a cause.

## Hardening

Applied 2026-11-01 as D-T47 (`docs/dev/testbed/TESTBED_REVIEW.md`), with
two departures from the suggestions below. The tag repair is general, so
any spaced tag name with no `=` is fixed, not only `job ids`. `%lt;` is
rewritten for every flavour, not keyed on version: on OCS the rewrite
only alters a value in which the user typed `%lt;` literally, and keying
on version would mean probing it at runtime. Anything the repairs do not
fix raises `SchedulerOutputError` instead of reading as empty. The
captures are `qstat_xml_defects.xml` and `qstat_j_xml_defects.xml`
(scenario `xml_defects`). Items 1-4 below were the original suggestions.

### Suggestions as written

1. **Strip XML-illegal characters before parsing.** Remove C0 controls
   except tab, LF and CR in `_parse_xml_root`. This turns defect 3 from "no
   details" into "one env value missing a character", for every flavour.
2. **Decode `%lt;` for gridengine 8.1.9 only.** Do it in the fields the
   parser reads, keyed on the flavour or version, so OCS output is not
   altered (see defect 2).
3. **Narrow the `_SGE_BAD_TAG_RE` comment** to the direct-add message path,
   and note the `<ulong_sublist>`, bare and OCS `<element>` shapes.
4. **Add captures** for an env value with a control character and for
   `<` in a name, argument and env value. Then the unit suite pins both
   defects against real output instead of typed text.

## Sources

- gridengine 8.1.9 source and Debian packaging:
  <https://deb.debian.org/debian/pool/main/g/gridengine/>
- Open Cluster Scheduler: <https://github.com/hpc-gridware/clusterscheduler>
- Oracle patch 123038-01 readme: <https://getupdates.oracle.com/readme/123038-01>
- Open OnDemand thread:
  <https://discourse.openondemand.org/t/active-jobs-with-ssge-8-1-9-request-for-jobs-failed-due-to-body-parsing-error/704>
- StarCluster thread:
  <https://mailman.mit.edu/pipermail/starcluster/2012-January/000980.html>
