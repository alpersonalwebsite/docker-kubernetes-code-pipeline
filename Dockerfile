# Pinned, and to a supported base. `FROM python:stretch` named no Python version and no
# digest, so the image built from a moving target: Debian stretch reached end of life in
# June 2022 and the tag still resolves today, which means it kept shipping an unsupported
# OS silently rather than failing.
#
# Why 3.9 rather than something newer: pytest is pinned at 6.2.2, and its assertion
# rewriting breaks on Python 3.10+ with `TypeError: required field "lineno" missing from
# alias` at collection. Measured across containers: the suite passes on 3.7 and 3.9, fails
# to collect on 3.10 through 3.13, and passes again on 3.11-3.13 with `--assert=plain`,
# which disables the rewriting and the readable failure output that comes with it.
#
# The app itself is not the constraint. It serves every endpoint correctly on 3.11 and
# 3.12, verified directly. 3.9 keeps one Python across the image, the buildspec and local
# development, and bookworm is Debian 12, supported until 2028.
FROM python:3.9-slim-bookworm

# Dependencies before the source, so editing main.py does not invalidate the pip layer.
WORKDIR /app
COPY requirements.txt ./

# --no-cache-dir: the wheel cache is never reused in an image and only adds weight.
# Upgrading pip in its own layer was dropped: it re-resolves on every build, so the pinned
# requirements below stop being the only thing that decides what is installed.
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# A non-root user. The original ran as root, which is the container default and the reason
# simple_jwt_api.yml could not set runAsNonRoot. Nothing here writes to disk, so the
# account owns nothing and the filesystem can be mounted read-only.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER 10001

EXPOSE 8080

# --access-logfile -: gunicorn logs nothing by default, so requests were invisible in
# CloudWatch. Sent to stdout, which is where a container's logs belong.
ENTRYPOINT ["gunicorn", "-b", ":8080", "--access-logfile", "-", "main:APP"]
