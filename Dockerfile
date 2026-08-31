# Mallow — container image.
#
# The image is built but never pushed from here: creating cloud resources costs
# money and leaves external state, so that step stays behind its own
# authorization (see deploy/DEPLOY.md).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    GEMINI_LOCATION=global \
    MALLOW_DATA_DIR=/tmp/mallow

# A font that covers Traditional Chinese *and* Latin in one face. The export
# draws both in the same paragraph, and ReportLab uses one font per paragraph:
# a CJK-only fallback face silently drops every digit and English word. The
# export refuses to build rather than ship a half-empty document, so this line
# is load-bearing.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-wqy-zenhei \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the product runs on. The retired corpus, the QA package and the
# spike's own tests are excluded by .dockerignore rather than trusted to habit.
COPY mobile/    ./mobile/
COPY spike/     ./spike/
COPY assets/    ./assets/

# One worker, several threads: the work per request is a single upstream model
# call, so threads keep the container small without serialising people behind
# each other.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 \
         --chdir mobile server:app
