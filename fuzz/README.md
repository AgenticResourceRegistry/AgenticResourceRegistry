<!-- SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Fuzzing

Agentic Resource Registry is fuzzed with [Atheris](https://github.com/google/atheris) and is being
submitted to [Google OSS-Fuzz](https://google.github.io/oss-fuzz/) for continuous
fuzzing; the upstream project is not live yet.
The fuzz targets live here so they are versioned alongside the code they
exercise; OSS-Fuzz only stores the three-file project configuration, which is
mirrored under `fuzz/oss-fuzz/`.

## Targets

| Target | Trust boundary |
| --- | --- |
| `session_jsonl_fuzzer` | Harness session transcripts: `ingest_classify` on the write path, `parse_raw_events` on the read path |
| `session_structure_fuzzer` | Same pipeline, driven by Hypothesis-generated transcript records instead of raw bytes |
| `secrets_redactor_fuzzer` | `services.secrets_redactor.redact_secrets`, applied to every line and preview before storage |
| `support_redaction_fuzzer` | `observal_cli.support.redaction`, the single chokepoint for `observal doctor support bundle` |

Every target runs entirely in-process. None of them opens a socket, reads
credentials, touches PostgreSQL, ClickHouse or Redis, or depends on the clock,
so a crash reproduces from its input alone.

`session_jsonl_fuzzer` reads the first input byte as a harness selector
(`byte % len(HARNESSES)`) and treats the rest as the transcript, which is why
each seed corpus file starts with a single digit and is otherwise plain JSONL.

## Running a target locally

```bash
pip install atheris hypothesis
python3 fuzz/session_jsonl_fuzzer.py -atheris_runs=100000
```

Point libFuzzer at the seed corpus and dictionary to start from useful inputs
and keep anything new it discovers:

```bash
python3 fuzz/session_jsonl_fuzzer.py \
  -dict=fuzz/dictionaries/session_jsonl_fuzzer.dict \
  /tmp/observal-corpus fuzz/corpus/session_jsonl_fuzzer
```

`session_structure_fuzzer` is a polyglot. Run it under pytest to replay and
shrink any example Hypothesis has already recorded:

```bash
pytest fuzz/session_structure_fuzzer.py
```

`make test-fuzz` runs a short campaign over every target and is the quickest
way to confirm the harnesses still build after a parser change.

## Reproducing a crash

A crashing input is a plain file. OSS-Fuzz attaches one to every report;
locally libFuzzer writes `crash-<sha1>` into the working directory.

```bash
python3 fuzz/session_jsonl_fuzzer.py crash-<sha1>
```

Atheris prints the Python traceback and exits non-zero. For an OSS-Fuzz report,
`infra/helper.py reproduce observal <target> <testcase>` runs the same input
inside the container the report came from.

## Adding a target

1. Add `fuzz/<name>_fuzzer.py`. `build.sh` globs `*_fuzzer.py`, so no build
   change is needed.
2. Import the code under test inside `with atheris.instrument_imports():` so
   Atheris can add coverage instrumentation as the modules load. Call
   `_paths.add_source_roots()` first if the target touches `observal-server`.
3. Define `TestOneInput(data: bytes)`, wire it up in `main()`, and keep the
   harness thin -- decode the input, call the boundary, assert the contract.
   Expected rejections (a decode error on malformed input) should return; every
   other exception is a finding.
4. Bound the input size. Slow inputs are reported as timeouts rather than as
   bugs.
5. Declare any third-party import in the `fuzz` dependency group in
   `pyproject.toml` and commit the refreshed `uv.lock`. OSS-Fuzz installs that
   group into the base image's system interpreter, which is the only
   environment PyInstaller resolves imports against; a package that is merely
   importable in a local virtualenv is silently omitted from the built target.
6. Add a seed corpus under `corpus/<name>_fuzzer/` and a dictionary at
   `dictionaries/<name>_fuzzer.dict`. Both are optional and both are picked up
   automatically.
7. Corpus files are raw fuzzer input, not source. Nothing may rewrite them.
   `scripts/update_spdx_copyright.py` skips `fuzz/corpus/**`, and `REUSE.toml`
   carries their licensing instead. `session_jsonl_fuzzer` reads
   its first byte, so a stray header would repoint the whole corpus at one
   parser; `tests/test_fuzz_targets.py` asserts every parser still has a seed.
8. Keep seed corpora free of anything that looks like a credential. The
   repository runs Gitleaks and a pre-commit secret scan; put distinctive vendor
   prefixes in the dictionary, where they are too short to match a scanner rule,
   and use zero-entropy placeholders in corpus files.

Prefer extending an existing target over adding a near-duplicate. A new target
should reach a boundary the current four do not.

## Maintaining the OSS-Fuzz project

`fuzz/oss-fuzz/` mirrors `projects/observal/` in
[google/oss-fuzz](https://github.com/google/oss-fuzz). Edit the files under
`fuzz/oss-fuzz/`, then copy them upstream in the same change so the two never
drift.

Validate a change against the real builder before opening the upstream pull
request:

```bash
git clone --depth 1 https://github.com/google/oss-fuzz.git
cp -r fuzz/oss-fuzz oss-fuzz/projects/observal
cd oss-fuzz

python3 infra/helper.py build_image observal
python3 infra/helper.py build_fuzzers observal /path/to/AgenticResourceRegistry   # builds this checkout
python3 infra/helper.py check_build observal
python3 infra/helper.py run_fuzzer observal session_jsonl_fuzzer -- -runs=10000

python3 infra/helper.py build_fuzzers --sanitizer coverage observal /path/to/AgenticResourceRegistry
python3 infra/helper.py coverage observal --fuzz-target=session_jsonl_fuzzer
```

Passing the checkout path to `build_fuzzers` replaces the `git clone` in the
Dockerfile with the local tree, which is how you test targets before they land
on `main`. Two side effects to know about: the build writes `*.pkg.spec` into
the checkout, and the coverage build prepends a `coverage` stub to each target
source in place. Only `*.pkg.spec` and `coverage_wrapper.py` are gitignored.
Target sources are tracked, so inspect `git diff fuzz/` and restore any modified
targets before committing.

Once the first hosted build succeeds, add the status badge to the README:

```markdown
[![Fuzzing Status](https://oss-fuzz-build-logs.storage.googleapis.com/badges/observal.svg)](https://issues.oss-fuzz.com/issues?q=project:observal)
```
