#!/usr/bin/env bash
# Mallow, locally.
#
#   ./run.sh              real model   (needs GOOGLE_CLOUD_PROJECT + credentials)
#   ./run.sh demo         deterministic model, separate journal, no cloud needed
#   ./run.sh doctor       what is installed, what is missing, how to fix it
#   ./run.sh test         🔴 the release gate. Every suite runs, or this fails
#   ./run.sh test-python  the Python suites only, and it says what it skipped
#   ./run.sh seed-demo    put an invented week into the demo journal
#   ./run.sh leaf         run the scheduled reflection once, against the demo journal
set -euo pipefail
cd "$(dirname "$0")"

INSTALL_HINT='  python3 -m pip install -r requirements.txt pytest playwright pdfminer.six
  python3 -m playwright install chromium'

have_python_deps() {
  python3 - <<'PY' >/dev/null 2>&1
import importlib
# `pdfminer` is a test dependency, not a runtime one: the product writes PDFs
# and never reads them. It is probed here rather than added to
# requirements.txt, because the container has no reason to carry a PDF parser —
# but a gate that cannot read back what the export wrote is a gate that takes
# the export's word for it.
for m in ("pytest", "flask", "google.genai", "google.auth",
          "reportlab", "requests", "google.cloud.firestore", "pdfminer"):
    importlib.import_module(m)
PY
}

# 🔴 A different question from "is it importable here".
#
# `requirements.txt` pinned google-auth 2.35.0 while google-genai 2.19.0
# requires >=2.56.0. Every local run was fine, because this machine already had
# a compatible google-auth sitting there from before and pip had no reason to
# touch it. A clean install — which is the only kind the Docker build does —
# stopped at dependency resolution, before a single test or line of the app.
#
# So the gate asks pip to resolve the file, rather than asking this laptop
# whether it happens to work.
# `--ignore-installed` matters: without it pip may accept a pin because this
# machine already satisfies it, which is the exact blindness that let the
# google-auth conflict ship. The container starts from nothing, so the check
# has to as well.
requirements_resolve() {
  python3 -m pip install --dry-run --ignore-installed --quiet --report /dev/null \
      -r requirements.txt >/dev/null 2>&1
}

have_browser() {
  python3 - <<'PY' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch().close()
PY
}

py_suites() {
  echo "— extraction slice —"
  ( cd spike/voice && python3 -m pytest tests -q )
  echo "— product —"
  python3 -m pytest mobile/tests/test_product.py -q
}

case "${1:-real}" in
  doctor)
    echo "python3      : $(python3 -V 2>&1)"
    if have_python_deps; then echo "python deps  : ✅ present"
    else echo "python deps  : ❌ missing"; fi
    if requirements_resolve; then echo "requirements : ✅ resolves on a clean install"
    else echo "requirements : ❌ CONFLICTS — the Docker build would fail here"; fi
    if have_browser;     then echo "browser      : ✅ playwright + chromium"
    else echo "browser      : ❌ missing"; fi
    echo
    echo "To install everything the release gate needs:"
    echo "$INSTALL_HINT"
    exit 0
    ;;

  demo)
    export MALLOW_FAKE_MODEL=1
    # One fixed local workspace, so `seed-demo` and `leaf` and the browser all
    # land in the same place. Demo journal only; a Firebase uid comes out of a
    # token and this cannot reach it.
    export MALLOW_LOCAL_UID="${MALLOW_LOCAL_UID:-demo-owner}"
    echo "Mallow — demo mode. Deterministic model, demo journal, no cloud project."
    echo "  http://127.0.0.1:${PORT:-8080}          （加 ?lang=en 看英文版）"
    ;;

  test-python)
    # The honest partial run. It names what it did not cover rather than
    # letting a summary line imply everything passed.
    if ! have_python_deps; then
      echo "✋ The Python test dependencies are not installed."; echo "$INSTALL_HINT"; exit 1
    fi
    py_suites
    echo
    echo "🔴 browser suite: NOT RUN. This is ./run.sh test-python."
    echo "   102 browser tests were not executed — auth.js, and the silence"
    echo "   gate that only exists in a browser. Use ./run.sh test for the gate."
    exit 0
    ;;

  test)
    # 🔴 The release gate. It is allowed to fail; it is not allowed to lie.
    #
    # Two things used to let it report green while a suite had not run: the
    # browser tests were behind `pytest.importorskip`, and this case ended
    # their line with `|| echo`. Between them, a machine with no browser engine
    # produced a cheerful summary and exit 0. A gate that treats "did not run"
    # as "passed" is worse than no gate, because people trust it.
    #
    # So: everything is probed first, and a missing dependency stops the
    # command with instructions rather than quietly narrowing what was checked.
    missing=0
    have_python_deps || { echo "✋ Python test dependencies missing."; missing=1; }
    have_browser     || { echo "✋ playwright and/or chromium missing."; missing=1; }
    if ! requirements_resolve; then
      echo "✋ requirements.txt does not resolve on a clean install."
      echo "   Everything here would still pass, and the Docker build would not"
      echo "   reach the first line of the app. Run this to see the conflict:"
      echo "     python3 -m pip install --dry-run --ignore-installed -r requirements.txt"
      missing=1
    fi
    if [ "$missing" -ne 0 ]; then
      echo
      echo "The release gate runs every suite or none. Install:"
      echo "$INSTALL_HINT"
      echo
      echo "To run only the Python suites on purpose:  ./run.sh test-python"
      exit 1
    fi

    py_suites
    echo "— browser —"
    python3 -m pytest mobile/tests/browser -q
    echo
    echo "✅ every suite ran."
    exit 0
    ;;

  seed-demo)
    # Invented records, demo journal only. Not in the image; see demo/seed.py.
    export MALLOW_FAKE_MODEL=1
    python3 demo/seed.py "${2:-demo-owner}"
    exit 0
    ;;

  leaf)
    # The scheduled task, run by hand against the demo journal, so the cadence
    # path can be watched without Cloud Scheduler. This is a developer
    # command: there is no button for it anywhere in the app, on purpose.
    export MALLOW_FAKE_MODEL=1
    export MALLOW_TASK_KEY="${MALLOW_TASK_KEY:-local-dev-key}"
    export MALLOW_LOCAL_UID="${MALLOW_LOCAL_UID:-demo-owner}"
    python3 - <<'PY'
import os, sys
sys.path[:0] = ["mobile", "spike/voice"]
import server
with server.app.test_client() as c:
    r = c.post("/tasks/reflections",
               headers={"X-Mallow-Task-Key": os.environ["MALLOW_TASK_KEY"]})
    print(r.status_code, r.get_data(as_text=True))
PY
    exit 0
    ;;

  real)
    : "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT, or run ./run.sh demo}"
    export GEMINI_LOCATION="${GEMINI_LOCATION:-global}"
    ;;
esac

exec python3 mobile/server.py
