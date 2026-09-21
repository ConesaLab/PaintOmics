# Security policy

PaintOmics accepts uploaded omics data, runs analyses on it, and stores the
results per user. It is also self-hostable, so a bug here can affect the public
instance at <https://paintomics.org/> and every deployment somebody else runs.
Reports are welcome.

## Reporting a vulnerability

Report privately, by either route:

- **GitHub private vulnerability reporting** — the *Security* tab of
  [ConesaLab/PaintOmics](https://github.com/ConesaLab/PaintOmics) →
  *Report a vulnerability*. This is the preferred route: the report, the
  discussion and the fix stay in one place.
- **Email** — [paintomicsai@gmail.com](mailto:paintomicsai@gmail.com), with
  `security` in the subject line.

Please do **not** open a public issue, pull request or discussion for a
vulnerability, and do not post details on a mailing list before a fix exists.

A useful report says:

- what an attacker gains — read another user's job, execute code on the server,
  escalate to admin, read a file outside the job directory;
- where the affected code is, if you know: a path in `PaintomicsServer/` or
  `PaintomicsClient/`, or the request that triggers it;
- how to reproduce it, ideally against a local deployment;
- which version you tested — the commit on `master`, or the public instance and
  roughly when.

### What happens next

PaintOmics is maintained by a small academic group, so this is a commitment we
can keep rather than a service level agreement:

- We acknowledge a report within a handful of working days.
- We tell you whether we can reproduce it, and what we think the impact is.
- We tell you when the fix lands on `master` and when the public instance is
  updated. Timing depends on severity and on who is available; we will not
  invent a deadline we cannot meet.
- We credit you in the commit message and release notes if you want to be
  credited, and leave you out if you do not.

There is no bug bounty. Nothing in this policy is a legal undertaking.

## Supported versions

| Version | Security fixes |
|---|---|
| `master` branch of this repository | Yes |
| The public instance, <https://paintomics.org/> | Yes |
| PaintOmics 4 (*Nucleic Acids Research*, 2022) | No |
| PaintOmics 3 and earlier | No |

There are no maintained release branches and nothing is backported. Fixes land
on `master`; a self-hosted deployment takes them by pulling `master` and
rebuilding. If you are running a PaintOmics 3 or 4 instance, the fix for
anything reported here is to move to the current code.

The classes of issue the project has already closed are listed in the
**Security** row of the *What's new* table in [`README.md`](README.md). They are
the kind of thing we want to hear about.

## Scope

In scope:

- The application code in this repository — the Flask server under
  `PaintomicsServer/`, the JavaScript client under `PaintomicsClient/`, the
  admin and installer tooling under `PaintomicsServer/src/AdminTools/`, and the
  container stack under `deploy/`.
- The deployed public instance at <https://paintomics.org/>.
- Anything that lets one user reach another user's jobs, uploaded files,
  results or account; anything that escapes a job's directory through a file or
  job name; anything that turns an uploaded data matrix into code execution;
  and the registration, session and password-reset flows.

Out of scope:

- **KEGG, Reactome and MapMan themselves.** The pathway databases, their
  content and their web services are third-party. Report problems with them to
  their maintainers. Wrong or outdated pathway annotation in PaintOmics is a
  data issue, not a vulnerability — send it to
  [paintomicsai@gmail.com](mailto:paintomicsai@gmail.com) or open an issue.
- **Findings that require an operator to have misconfigured their own
  deployment.** `deploy/README.md` names the constraints that must hold, and
  `deploy/smoke-test.sh` checks them — MongoDB must not be published to the
  host, Flask debug must be off, HTTP must redirect to HTTPS, and uWSGI must run
  a single process. A deployment that violates one of those is insecure by
  configuration; that is not a vulnerability in the code. The same goes for a
  host's own TLS, firewall or reverse-proxy setup, and for secrets an operator
  put somewhere they should not be.
- Reports from an automated scanner with no demonstrated impact, missing
  hardening headers with no exploit path, and best-practice advice unattached to
  a concrete attack.
- Resource exhaustion from submitting large or numerous analyses on a public
  instance. The analyses are deliberately long-running and the job queue is
  in-process; this is a capacity property of the design, not a defect. A way to
  make the server do unbounded work from a single small request *is* in scope.
- Vulnerabilities in third-party dependencies, unless you can show a path to
  them through PaintOmics code. Dependency versions are pinned in
  [`requirements.txt`](requirements.txt); if a pin is exploitable as shipped, say
  so and we will treat it as in scope.

### Testing

Please test against your own deployment wherever you can. If you must test
against <https://paintomics.org/>, do not run automated scanners or load
generators against it — it is a shared research instance — and do not access,
modify or delete data belonging to anyone else. If you discover that you *can*
reach another user's data, stop there and report it; you do not need to prove it
twice.

## For self-hosters

Every deployment must supply its own secrets. Nothing usable ships in the
repository, and the configuration template
`PaintomicsServer/src/resources/example_serverconf.py` reads every secret from
the environment with an empty default — a rule enforced by
`PaintomicsServer/src/tests/test_release_hygiene.py`.

Two example files say what to set:

- **`deploy/env.example`** → copy to `deploy/.env` for the container stack.
  `PAINTOMICS_BASE_URL` is required and the container refuses to start without
  it; it is embedded in activation emails, so a wrong value breaks registration
  silently. `SMTP_PASSWORD` is the SendGrid credential used for registration and
  password-reset mail. `AI_CSIC_API_KEY` is the LLM gateway token for the AI
  interpretation agent — leave it empty and set
  `AI_INTERPRETATION_ENABLED=false` to disable the feature cleanly.
  `AI_PUBMED_API_KEY` only raises an NCBI rate limit.
- **`PaintomicsServer/.env.example`** → copy to `PaintomicsServer/.env` for a
  from-source install. It carries the same AI keys for local work. A real
  environment variable always wins over this file, so a stray `.env` cannot
  override what a production deployment configured.

`.env`, `deploy/.env`, `PaintomicsServer/src/conf/serverconf.py` and
`deploy/nginx/certs/` are all gitignored. Keep them that way; the certificate
private key generated by `deploy/make-cert.sh` must never be committed.

Two further points that decide whether a deployment is safe:

- **MongoDB runs without authentication** and is reachable only on the Compose
  network. Do not add a `ports:` mapping for it.
- `deploy/make-cert.sh` produces a **self-signed** certificate, intended as an
  interim measure until the host has a DNS name and a trusted certificate.
  Browsers will warn, and HSTS stays off in `deploy/nginx/paintomics.conf` for
  that reason. Replace it with a real certificate before running an instance
  other people use.

Run `./deploy/smoke-test.sh` after every deployment and after every upgrade. It
exits non-zero on exactly the mistakes that are cheap to catch there and
expensive to find in production.
