# Bench jobs inbox

This branch is the hamlet bench runner's inbox (Jeff, 2026-11-17). Cloud threads push bench jobs
here; the runner on hamlet fetches the branch, checks every job against the bench rules
(docs/bench-runner.md on main) and writes each job's result back here.

    jobs/ID/job.json        a job, plus the files it names as "job:NAME"
    results/ID/result.json  queued | refused | running | done | failed | cancelled, with the reasons
    results/ID/runner.log   the run's log (every command and its output, UTC)
    results/ID/files/       the run folder's small files (files.json lists them all, with sha256)
    status.json             the runner: its commit, the running job, the queue, the last poll

Submit with `python3 tools/bench_inbox.py submit JOB.json` from a blowfang checkout on main;
read results with `python3 tools/bench_inbox.py show ID`. Don't edit results/ or status.json:
the runner owns them. This branch never merges into main.
