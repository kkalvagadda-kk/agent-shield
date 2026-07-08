1. **Traffic splitting / canary support** — split traffic % between two deployments of the same artifact on a shared endpoint. Enables gradual rollout without separate URLs.
2. **Deployment event log** — live stream of infra events alongside the overview (pulling image, creating pods, health check passed/failed, scaling events). Makes status transitions visible to the user rather than opaque badge changes.
3. **Blue-green rollback** — one-click rollback to previous version using `previous_version_id`. Drain in-flight requests before terminating old deployment.
