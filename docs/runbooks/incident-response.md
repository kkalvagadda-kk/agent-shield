# Incident Response Runbook

When something looks wrong, move fast. This tells you what to do.

---

## 1. Triage

Start with OPA decisions — that's the fastest signal.

```bash
# Check OPA decisions for a specific agent
curl -s http://localhost:8000/api/v1/opa-decisions/?agent=<agent-name> | jq .

# Check agent status in the registry
curl -s http://localhost:8000/api/v1/agents/<agent-name> | jq '.status'

# List all agents with non-active status
curl -s http://localhost:8000/api/v1/agents/ | jq '.[] | select(.status != "active") | {name, status}'
```

Look for: repeated OPA denials, status `error` or `suspended`, unusual request volumes.

```bash
# Check agent pod state
kubectl get pods -n agents-<team> -l agentshield.io/team=<team>

# Quick log tail
kubectl logs -n agents-<team> -l agentshield.io/team=<team> --tail=100
```

---

## 2. Quarantine

Quarantine preserves the pod for forensics — it does NOT kill the process.

```bash
curl -s -X POST http://localhost:8000/api/v1/agents/<agent-name>/quarantine \
  -H "Authorization: Bearer $TOKEN" | jq .
```

The agent's status flips to `quarantined`. Traffic is blocked at the policy layer. The pod keeps running so you can inspect it.

Verify:

```bash
curl -s http://localhost:8000/api/v1/agents/<agent-name> | jq '.status'
# should return "quarantined"
```

---

## 3. Evidence Collection

Collect everything before touching the pod.

**Agent logs:**

```bash
kubectl logs -n agents-<team> -l agentshield.io/team=<team> > /tmp/<agent-name>-logs.txt
```

**OPA decision log:**

```bash
curl -s "http://localhost:8000/api/v1/opa-decisions/?agent=<agent-name>&limit=500" \
  > /tmp/<agent-name>-opa-decisions.json
```

**Langfuse trace export** — go to the Langfuse UI, filter by agent name or session ID, export as JSON. Or use the Langfuse API:

```bash
# Replace LANGFUSE_URL and session ID as appropriate
curl -s "$LANGFUSE_URL/api/public/sessions/<session-id>" \
  -H "Authorization: Basic $(echo -n $LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY | base64)" \
  > /tmp/<agent-name>-traces.json
```

**Pod filesystem snapshot (optional):**

```bash
# Copy any relevant files out before you touch the pod
kubectl cp agents-<team>/<pod-name>:/app/logs /tmp/<agent-name>-pod-logs/
```

---

## 4. Incident Timeline

Fill this in as you go. Retroactive timelines are unreliable.

| Time (UTC) | Action | By | Notes |
|------------|--------|----|-------|
| HH:MM | Incident detected | | Source: OPA alert / user report / monitoring |
| HH:MM | Agent quarantined | | `POST /api/v1/agents/<name>/quarantine` |
| HH:MM | Logs collected | | Saved to /tmp/<name>-logs.txt |
| HH:MM | Root cause identified | | |
| HH:MM | Remediation applied | | |
| HH:MM | Agent restored / terminated | | |

---

## 5. Reopen Approval

If an approval request timed out and needs reprocessing:

```bash
# List pending/timed-out approvals for the agent
curl -s "http://localhost:8000/api/v1/approvals/?agent=<agent-name>&status=timeout" | jq .

# Reopen a specific approval by ID
curl -s -X POST http://localhost:8000/api/v1/approvals/<approval-id>/reopen \
  -H "Authorization: Bearer $TOKEN" | jq .
```

After reopening, the approval re-enters the queue with a fresh timeout window. Notify the approver.

---

## 6. Escalation Contacts

TODO: fill in oncall contacts

```
Primary oncall:    TODO
Secondary oncall:  TODO
Security team:     TODO
Platform lead:     TODO
Pagerduty runbook: TODO
```

If you can't reach anyone and the agent is causing active harm, delete the pod directly:

```bash
kubectl delete pod -n agents-<team> <pod-name>
```

This is the nuclear option. The quarantine API is always preferred because it keeps forensic state.
