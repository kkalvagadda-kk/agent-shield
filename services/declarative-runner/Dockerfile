FROM python:3.12-slim
WORKDIR /app
COPY services/declarative-runner/requirements.txt .
COPY sdk/ /tmp/sdk/
RUN pip install --no-cache-dir /tmp/sdk/ && pip install --no-cache-dir -r requirements.txt && rm -rf /tmp/sdk
COPY services/declarative-runner/ .
ENV AGENT_NAME=declarative-agent
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
