# TC observability configuration

This directory contains a Prometheus scrape example and recording-free alert rules. It does not
configure an Alertmanager receiver and does not prove delivery to an on-call system.

1. Mount `prometheus-rules.yml` at `/etc/prometheus/rules/tc-prometheus-rules.yml` and validate it
   with the production Prometheus version (`promtool check rules ...`).
2. Scrape the internal API at `GET /api/metrics`; do not expose this route through the public Slack
   ingress.
3. Set `TC_WORKER_METRICS_PATH` to an absolute `.prom` file in a node-exporter textfile directory,
   for example `/var/lib/node_exporter/textfile_collector/tc-worker.prom`.
4. Use one textfile directory/node-exporter scrape target per worker. The metrics intentionally omit
   worker IDs, hostnames, pod names, and other high-cardinality identifiers.
5. Add receiver and routing configuration only in the deployment secret/configuration system, then
   perform a controlled test alert and record receiver acknowledgement separately.

The full semantics, metric inventory, privacy restrictions, and response runbook are in
[`docs/phase-f/sprint-f1-observability.md`](../../docs/phase-f/sprint-f1-observability.md).
