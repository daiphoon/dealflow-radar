# syntax=docker/dockerfile:1.7

FROM postgres:16-bookworm

RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install --yes --no-install-recommends age ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY deploy/backup.sh /usr/local/bin/dealflow-backup
COPY deploy/restore-test.sh /usr/local/bin/dealflow-restore-test

RUN chmod 0555 /usr/local/bin/dealflow-backup /usr/local/bin/dealflow-restore-test
