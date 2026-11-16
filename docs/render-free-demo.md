# KD Checker PDF demo on Render Free

Use only synthetic or independently anonymized PDF copies. Review text layers,
metadata, attachments, hidden content and identifiers before uploading. The
demo does not anonymize uploads or prove that a PDF is safe. All testers with
the shared credential can view, export and clear this instance's history.
Use a separate service and credential for this demonstration.

## Before deployment

1. Review the Draft PR and select its exact published commit. It is based on
   #93, including the worker fix from #92 and PDF acceptance. The CSV matcher
   in #88, direct analyzer tests in #89, Windows launcher in #90, instructions
   in #91 and opt-in evidence in #94 are separate changes.
2. In an approved local or CI environment, install `requirements.txt` and
   `requirements-dev.txt`. On Linux, run
   `python -m pytest -q tests/test_render_demo.py tests/test_parser_worker_pdf_bundle.py`.
   This includes the ASGI multipart, export and reset test. Resolve any failure
   before creating a Render service.
3. Review the Render workspace's monthly included usage and billing settings.
   Use a workspace without a payment method where possible to avoid paid
   overages. Free usage still consumes included build and transfer quotas.
   With a payment method, excess usage can be charged. Where supported,
   check the spend limit before authorizing deployment. Stop if the proposed
   service or any associated resource requires a paid plan.
   [Render Free limits](https://render.com/docs/free)

## Deploy only after approval

1. In the Render Dashboard, connect GitHub with access to this repository
   only. Select **New → Web Service** and the reviewed feature branch
   `feature/render-free-pdf-demo`. Select **Python** and **Free**. Use the
   build and start commands from `render.yaml`. Set **Auto-Deploy** to off,
   including if you use the Blueprint instead. Creating a service or applying
   a Blueprint can start an initial deploy. Review the plan before applying it.
   Provision one web instance. Leave databases, persistent disks, custom
   domains and other add-ons unset.
   [Render Web Services](https://render.com/docs/web-services) ·
   [Blueprint reference](https://render.com/docs/blueprint-spec)
2. Set `PYTHON_VERSION=3.13.12`. In a trusted local terminal generate a unique
   credential with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   Enter it only in the service's secret environment variable `KD_DEMO_TOKEN`.
   Enter `KD_DEMO_ACK=anonymized-pdf-only` after the team accepts the input
   restrictions. Do not reuse a password, write the token to Git, or share it
   by URL. Render provides `PORT` and `RENDER_EXTERNAL_HOSTNAME`.
   Do not attach environment groups with Checklist or Supabase settings.
   [Render environment variables](https://render.com/docs/environment-variables)
3. Set the HTTP health check to `/healthz` and use the provided HTTPS
   `*.onrender.com` address. Render terminates TLS and redirects public HTTP.
   The application accepts only the configured hostname and does not trust
   forwarded headers. Use this deployment behind Render's HTTPS ingress.
   [Render TLS](https://render.com/docs/tls) ·
   [Render health checks](https://render.com/docs/health-checks)
4. Verify a guest gets 401 from the interface, upload, history and exports.
   The public health endpoint returns only 204. Open the HTTPS URL in a
   private browser window, use the username `demo` and the secret as the
   password. Upload a synthetic bundle. Verify findings, CSV, XLSX,
   unsupported-file refusal, limit refusal, and the history reset. Check that
   cross-origin uploads and resets are refused. Share the URL and credential
   only after these checks pass.

## Data lifetime and limits

- The service accepts at most four PDFs per request, 2 MiB each and 6 MiB
  combined, with a bounded multipart body. The parser runs in a separate
  process with a time, memory and output budget. Its resource limits are
  not a general isolation boundary for malicious files.
- There are no external data sources or databases. Up to 12 checks are kept
  in memory. Uploaded bytes are transient, and temporary files are created
  inside the instance's private temporary directory. There is no persistent
  disk. Render Free can spin down or restart, so a new process starts with
  empty history. Export results you are authorized to retain.
  [Render Free limitations](https://render.com/docs/free)
- Use **Очистить всю историю стенда** to clear this process's findings and
  filenames. Testers' downloaded reports are separate copies. After testing,
  close private browser windows, rotate the token, and suspend or delete the
  service if it is no longer needed. Reset and shutdown do not provide a
  guarantee about forensic recovery, provider logs, caches or backups.
