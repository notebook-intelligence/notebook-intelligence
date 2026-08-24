# Performance diagnostics

NBI can record where each chat turn spends its time and measure the machine it
is running on. This document covers turning that on, reading what it produces,
and the three diagnoses it was built to make.

It is off by default. When off, the cost is one boolean check per
instrumentation site, so there is nothing to weigh up before shipping it.

Operators looking for the environment variables and fleet policy should read
the [Performance diagnostics section of the admin guide](admin-guide.md#performance-diagnostics)
instead; this document is for whoever is holding the slow machine.

## When to reach for this

Use it when NBI feels slower in one deployment than in another and you cannot
say why. The question it answers is "which phase of the turn is expensive," and
the follow-up question it answers is "is that phase expensive because of this
machine." Typical causes it is built to separate:

- an internal LLM gateway that adds latency in front of the model
- `~/.claude` or the Jupyter home directory on a network filesystem
- a TLS-intercepting proxy
- CPU throttling in a container

If NBI is slow for everyone everywhere, this will tell you the model is slow,
which you probably already knew.

## Turning it on

Open NBI Settings and select the **Performance** tab.

| Control              | What it does                                                                                                                                               |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Enabled**          | Starts recording. Takes effect immediately; no restart.                                                                                                    |
| **Log to file**      | Also appends each turn to a JSON Lines file. See [Collecting across sessions](#collecting-across-sessions-and-machines).                                   |
| **Attribute detail** | `redacted` (default) hashes file basenames and model, tool, and server names. `full` records them as written. Timings and counts are identical either way. |

If the controls are greyed out, an administrator has locked them. The tooltip
says so.

Every recorded turn is also written to the Jupyter server log as a single
`perf turn ...` INFO line, so a headless install gets the same signal with no
UI at all:

```
perf turn message_id=abc mode=claude status=ok total=8.2s active=8.2s
  context_prep=0.3s connect=4.1s first_token=4.9s stream=3.6s tools=0.0s
  api=2.9s stalls=1 dropped_spans=0
```

## Reading a turn

The **Recent turns** table holds the most recent turns (50 by default). Each
column has a tooltip; the ones worth internalizing:

| Column          | Meaning                                                                                                    |
| --------------- | ---------------------------------------------------------------------------------------------------------- |
| **Total**       | Request arriving to response finishing, including time waiting on you.                                     |
| **Active**      | Total minus time blocked on your input. Judge performance by this, not Total.                              |
| **Spawn**       | The `connect` span: agent subprocess and CLI startup.                                                      |
| **First token** | Offset from turn start to the first content-bearing chunk. Local progress spinners do not count toward it. |
| **Stream**      | First content chunk to last. Excludes connect.                                                             |
| **Stalls**      | Gaps between stream chunks over the stall threshold, each tagged with what preceded it.                    |
| **API ms**      | Duration the SDK attributes to the model API.                                                              |

**The single most useful comparison is Active against API ms.** When they are
close, the turn's time is on the far side of the network, in the gateway or the
model. When they diverge, the time is local, and the Spawn, First token, and
Tools columns say where.

The **Verdict** column applies that reasoning for you. It is derived from the
other columns, not recorded by the server:

| Verdict             | Trigger                                  | What to look at next                                                   |
| ------------------- | ---------------------------------------- | ---------------------------------------------------------------------- |
| Model or gateway    | API ms is at least 70% of Active         | Gateway configuration, model choice, or the network check in the probe |
| Tool execution      | Tool spans are at least 40% of Active    | Expand the row for the slowest tool                                    |
| Agent cold start    | `connect` is at least 25% of Active      | The probe's subprocess group and `~/.claude` filesystem rows           |
| Context preparation | `context_prep` is at least 25% of Active | The probe's filesystem group; the size of your ruleset and skills tree |
| Mid-stream stalls   | Any stall events                         | Expand the row; each stall is tagged `after=tool_use` or `after=text`  |
| No single hotspot   | None of the above                        | The turn is broadly slow rather than blocked on one phase              |

Expanding a row shows every span in the turn as a proportional bar with its
attributes, followed by the events with their offsets from turn start.

### Span reference

| Span           | Measures                                                                                                                            | Attributes                                        |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `context_prep` | Rule and skill discovery, plus context assembly, before the request is dispatched. Filesystem-bound on the server's home directory. | `rule_count`, `skill_count`, `file_count`, `file` |
| `dispatch`     | Handing the request to the resolved participant.                                                                                    | `provider`                                        |
| `connect`      | Agent subprocess and CLI startup. `cold=true` on the first connect of a session.                                                    | `cold`, `gateway_host`                            |
| `stream`       | First content-bearing chunk to the end of the response.                                                                             | `chunk_count`, `bytes`                            |
| `tool:<name>`  | One tool call. In `redacted` mode the name after `tool:` is hashed for third-party tools and left readable for NBI's own.           | `tool`, `server`, `ok`                            |
| `ui_command`   | A round trip to the browser to run a UI command.                                                                                    | `command`                                         |

### Event reference

Events are point-in-time marks carrying `t_ms`, an offset from the start of the
turn.

| Event         | Meaning                                                             | Attributes                               |
| ------------- | ------------------------------------------------------------------- | ---------------------------------------- |
| `first_token` | The first content-bearing chunk reached the browser.                | none                                     |
| `stall`       | A gap between streamed messages over the stall threshold.           | `gap_ms`, `after` (`tool_use` or `text`) |
| `egress`      | Written once at the end of the turn with the totals for the stream. | `count`, `bytes`                         |

A turn document also carries `dropped_spans` and `dropped_attrs`. Anything
above zero means the per-turn caps truncated the record, and the timeline you
are looking at is incomplete. The panel says so under an expanded row.

## Reading the probe

**Run probe** measures the machine rather than a turn. It writes and deletes
small temporary files in the directories it measures, and runs the filesystem,
subprocess, and contention groups one at a time so they do not measure each
other.

Each check is rendered with a band. `OK` means it is within the range a local
disk and a nearby endpoint deliver; `WARN` and `BAD` carry a sentence saying
what the number implies. A check that is `SKIPPED` did not apply to this
platform, and one that `TIMED OUT` did not return within its budget, which on a
network filesystem is itself the finding.

### Filesystem

| Check                | Reads                                                                          | Bands                                                                                                               |
| -------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| small-file latency   | Median `stat`, small read, and write+fsync+unlink over up to 20 iterations     | `stat` warns at 1 ms and is bad at 5 ms; write+fsync warns at 10 ms and is bad at 50 ms                             |
| sustained throughput | One 4 MB write-fsync-read-back pass                                            | Warns below 50 MB/s, bad below 10 MB/s                                                                              |
| mount                | Filesystem type and mount options for that directory                           | Warns on any network filesystem type (`nfs`, `nfs4`, `efs`, `cifs`, `smbfs`, `fuse`, `lustre`, `gpfs`, `glusterfs`) |
| session tree size    | File count and total bytes under `~/.claude/projects` and `~/.claude/sessions` | Warns over 5,000 files, bad over 20,000                                                                             |

The latency loop reports `first_iteration_ms` separately from the median in the
raw output, which separates cold cache from warm.

### Subprocess startup

`node --version`, `claude --version`, and `npm config get cache`, each timed.
Warns at 500 ms and is bad at 2 s. This is the number that turns into the Spawn
column on every turn, because cold start here is dominated by reading the
install tree.

### Process and host

Server process peak memory, host load average, cgroup CPU throttling, and the
Python interpreter version. `nr_throttled` above zero warns: a throttled
container makes local phases look slow for reasons unrelated to storage or the
network.

### Network (opt-in per run)

The network check is off unless you tick **Include network check**, and it asks
for confirmation showing the host it will contact. It makes several connections
to the single configured base URL's host:

1. a raw connection to time DNS, TCP, and the TLS handshake
2. a second raw connection to verify the presented certificate against the
   default trust store
3. one unauthenticated HTTP HEAD, retried as GET if the endpoint answers 405

Only the HTTP request carries the probe's `nbi-perf-probe/<version>`
User-Agent; the two raw TLS connections send no HTTP at all, so an allowlist
rule keyed on User-Agent alone will not cover them. A host-based rule covers
every leg.

It reports DNS, TCP, and TLS timings, whether the connection went through a
proxy, the presented certificate's issuer and subject CN, its SHA-256
fingerprints, whether it verified against the default trust store, the HTTP
status and time to first byte, clock skew against the endpoint, and which
proxy and CA-bundle environment variables are set.

An administrator can disable just this leg with `NBI_PERF_PROBE_NETWORK=off`,
which is the right posture when unauthenticated requests to the model gateway
would page your SOC.

## Three diagnoses

### The gateway is slow

**Signature.** Turn verdicts read _Model or gateway_. API ms tracks Active
closely. Spawn and context prep are small. In the probe, DNS, TCP, and TLS are
each fast, so the cost is not connection setup.

**Confirm.** Run several turns with different prompt sizes. If API ms scales
with output length, the model is the cost; if it is a flat overhead on every
turn regardless of size, the gateway is adding it.

**Next.** Compare against the same model through a different route if you have
one. The probe's `ttfb_ms` on an unauthenticated request is a floor, not the
authenticated latency, so treat it as a lower bound.

### The home directory is slow

**Signature.** Turn verdicts read _Agent cold start_ or _Context preparation_.
Spawn is seconds rather than hundreds of milliseconds. In the probe, the mount
row for `~/.claude` or the Jupyter root shows `nfs4` or another network
filesystem, small-file latency shows write+fsync medians in the tens or
hundreds of milliseconds, and sustained throughput is in single-digit MB/s.

**Confirm.** Single-digit MB/s alongside high fsync latency on an `nfs4` mount
is the EFS burst-credit-exhaustion signature. Confirm it from CloudWatch
(`BurstCreditBalance`) rather than from inside the pod, since the pod cannot
see the credit balance.

**Next.** Check the session tree size row. A large `~/.claude/projects` makes
every agent start pay to walk it, and on a network filesystem that is often
most of the spawn cost. If you enable **Log to file**, point
`NBI_PERF_LOG_DIR` at node-local scratch, or the diagnostics will write to the
same slow filesystem you are diagnosing.

### TLS is being intercepted

**Signature.** In the probe's network row,
`verified_against_default_bundle` is `false`, and the issuer CN names something
that is not a public CA. That is interception, and the issuer CN names the
interceptor. Alternatively the handshake fails outright with a `tls_error`,
which usually means the interception certificate is not in the trust store
this process uses.

**Confirm.** Compare the `fingerprint_sha256` values against the certificate
the endpoint serves from outside the network.

**Next.** The trust store the Python process uses and the one the Claude CLI
(node) uses are not the same. The probe reports whether `SSL_CERT_FILE`,
`REQUESTS_CA_BUNDLE`, and `NODE_EXTRA_CA_CERTS` are set, which is usually
enough to see which of the two is missing the interception root.

## Collecting across sessions and machines

Turning on **Log to file** appends each turn document as one JSON line under
`<nbi-dir>/perf/`, in a file named `perf-YYYYMMDD.jsonl`. The directory is
created `0700` and the files `0600`.

The first line of each new file is a metadata record rather than a turn:

```json
{ "meta": { "schema_version": 1, "log_fs_type": "apfs" } }
```

`log_fs_type` is the filesystem the log itself landed on, which makes a
misplaced log visible in the log. Every subsequent line is one turn:

```json
{
  "turn_id": "…",
  "message_id": "…",
  "mode": "claude",
  "model": "3f8a1c2d",
  "status": "ok",
  "t_wall": 1755907200.123,
  "total_ms": 8213.4,
  "active_ms": 8213.4,
  "spans": [
    {
      "name": "connect",
      "dur_ms": 4102.6,
      "status": "ok",
      "attrs": { "cold": true }
    }
  ],
  "events": [{ "name": "first_token", "t_ms": 4903.1, "attrs": {} }],
  "tokens": { "input": 27217, "output": 412 },
  "sdk": { "duration_ms": 8100, "duration_api_ms": 2900, "num_turns": 1 },
  "dropped_spans": 0,
  "dropped_attrs": 0
}
```

`status` is one of `ok`, `error`, or `cancelled`; a cancelled turn is recorded
as cancelled rather than as a fast success, so it will not skew your
percentiles downward. `t_wall` is epoch seconds as a float.

Writes are batched off the request path on a dedicated thread. If the
filesystem misbehaves the sink tolerates the failures and then disables itself
rather than blocking a turn; saving the settings again retries it. Retention is
capped at 50 MB total and 14 days, enforced when a new day's file is created.

On a network home directory, set `NBI_PERF_LOG_DIR` to node-local scratch (for
example `/tmp/nbi-perf`).

## What is recorded, and what never is

Recorded: durations, counts, byte sizes, status enums, token counts, and
tool, server, model, and provider names.

Never recorded: prompt or response text, file contents, absolute paths,
environment variable values, API keys, exception messages, or hostnames.

This is enforced structurally rather than by convention. The recorder checks
every attribute against a fixed per-span allowlist and drops anything not on
it, counting the drops in `dropped_attrs`. In the default `redacted` mode it
additionally hashes file basenames (keeping the extension, so
`notebook.ipynb` becomes `a1b2c3d4.ipynb`) and hashes model, tool, server, and
provider names in full, including tool names embedded in span names.

The probe output is separately scrubbed: home directory paths and the resolved
real home are replaced with `~`, and the username is replaced with `~user`.
Probe output carries `contains_internal_hostnames: true` when the network check
ran, because the configured gateway host appears in its own rows. The turns
report never contains a hostname, and the panel's **Copy as JSON** omits the
probe target for that reason.

## Filing a support ticket

Attach:

1. **Copy as JSON** from Recent turns, with several representative slow turns
   in the buffer.
2. **Copy as JSON** from the probe, having run it on the affected machine.
   Include the network check if your policy allows it, since it is the only
   thing that shows interception.
3. Which verdict the slow turns show, and what you expected instead.

Check the attribute detail setting first. `redacted` is safe to send as-is;
`full` contains model, tool, and server names and file basenames in the clear.
Neither mode contains prompt text, response text, or hostnames.
