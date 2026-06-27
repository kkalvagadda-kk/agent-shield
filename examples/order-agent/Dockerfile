FROM python:3.12-slim

WORKDIR /app

# Install local AgentShield SDK first (not from PyPI — use repo source)
COPY sdk/ /sdk/
RUN pip install --no-cache-dir /sdk/

# Install agent-specific deps (agentshield-sdk excluded — installed above)
COPY examples/order-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY examples/order-agent/agent.py .
COPY examples/order-agent/agent.yaml .

# Identity injected at deploy time by the deploy controller,
# but set a default for local builds.
ENV AGENT_NAME=order-agent

EXPOSE 8080

CMD ["agentshield", "dev", "--port", "8080"]
