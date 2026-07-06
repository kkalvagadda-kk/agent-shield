import kubernetes.client as k8s_client

# Namespace where the OPA config ConfigMap lives (shared across all agents)
_OPA_CONFIG_CM = "opa-sidecar-config"
_OPA_CONFIG_NS = "agentshield-platform"


def build_service(
    agent_name: str,
    environment: str,
    namespace: str,
    labels: dict,
) -> k8s_client.V1Service:
    """Build a ClusterIP Service that selects the agent pod.

    The service name mirrors the Deployment name so Envoy HTTPRoutes can reference
    it as a backendRef by name.
    """
    svc_name = f"{agent_name}-{environment}"
    return k8s_client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=k8s_client.V1ObjectMeta(
            name=svc_name,
            namespace=namespace,
            labels=labels,
        ),
        spec=k8s_client.V1ServiceSpec(
            selector={"app.kubernetes.io/name": agent_name},
            ports=[
                k8s_client.V1ServicePort(
                    name="http",
                    port=8080,
                    target_port=8080,
                    protocol="TCP",
                )
            ],
            type="ClusterIP",
        ),
    )


def build_httproute(
    agent_name: str,
    environment: str,
    namespace: str,
    team: str,
    gateway_namespace: str = "agentshield-platform",
) -> dict:
    """Build a gateway.networking.k8s.io/v1 HTTPRoute manifest as a plain dict.

    Routes /agents/{agent_name}/ → agent pod Service in agents-{team} namespace.
    """
    svc_name = f"{agent_name}-{environment}"
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": {
            "name": f"agent-{agent_name}-route",
            "namespace": gateway_namespace,
            "labels": {
                "agentshield.io/agent-name": agent_name,
                "agentshield.io/managed-by": "deploy-controller",
            },
        },
        "spec": {
            "parentRefs": [
                {
                    "name": "agentshield-gateway",
                    "namespace": gateway_namespace,
                }
            ],
            "rules": [
                {
                    "matches": [
                        {
                            "path": {
                                "type": "PathPrefix",
                                "value": f"/agents/{agent_name}/",
                            }
                        }
                    ],
                    "backendRefs": [
                        {
                            "name": svc_name,
                            "namespace": namespace,
                            "port": 8080,
                        }
                    ],
                }
            ],
        },
    }


def build_deployment(
    deployment: dict,
    agent: dict,
    version: dict,
    opa_image: str,
    registry_api_url: str = "http://agentshield-registry-api.agentshield-platform:8000",
) -> k8s_client.V1Deployment:
    """
    Build a V1Deployment manifest for the given agent deployment record.

    Phase 9.1 changes:
    - OPA sidecar now polls the central bundle server (not per-agent ConfigMap).
    - Pod runs under a per-agent ServiceAccount (agent-{name}-sa).
    - Projected bound SA token is mounted at /var/run/secrets/sa-token/token
      (audience=agentshield-opa, TTL=1h). The SDK opa_client reads it and
      includes it in every OPA decision request so the policy can verify identity.
    - agent_class env var injected so the SDK knows which OPA flow to use.

    Args:
        deployment: Deployment record from Registry API
        agent:      Agent record from GET /api/v1/agents/{name}
        version:    Version record from GET /api/v1/versions/{id}
        opa_image:  OPA sidecar image (e.g. openpolicyagent/opa:0.69.0-static)
    """
    agent_name = agent["name"]
    environment = deployment["environment"]
    team = agent.get("team", "platform")
    namespace = deployment["k8s_namespace"]
    replicas = deployment.get("replicas", 1)
    agent_class = agent.get("agent_class") or "user_delegated"

    k8s_name = f"{agent_name}-{environment}"
    image_tag = version["image_tag"]
    version_number = str(version.get("version_number", version.get("id", "unknown")))
    sa_name = f"agent-{agent_name}-sa"

    labels = {
        "app.kubernetes.io/name": agent_name,
        "app.kubernetes.io/version": version_number,
        "agentshield.io/team": team,
        "agentshield.io/environment": environment,
        "agentshield.io/agent-class": agent_class,
    }

    # --- Base env vars ---
    env_vars = [
        k8s_client.V1EnvVar(name="AGENT_NAME", value=agent_name),
        k8s_client.V1EnvVar(name="AGENT_VERSION", value=version_number),
        k8s_client.V1EnvVar(name="OPA_URL", value="http://localhost:8181"),
        k8s_client.V1EnvVar(name="AGENTSHIELD_OPA_URL", value="http://localhost:8181"),
        # Phase 9.1: agent_class tells SDK which OPA authorization flow to use
        k8s_client.V1EnvVar(name="AGENTSHIELD_AGENT_CLASS", value=agent_class),
        # Phase 9.1: SA token path (projected volume, see volumes below)
        k8s_client.V1EnvVar(
            name="AGENTSHIELD_SA_TOKEN_PATH",
            value="/var/run/secrets/sa-token/token",
        ),
        # Playground context — false for production deployments
        k8s_client.V1EnvVar(name="AGENTSHIELD_PLAYGROUND", value="false"),
        k8s_client.V1EnvVar(name="AGENTSHIELD_SANDBOX", value="false"),
    ]

    # --- LLM provider env vars ---
    llm_secret_name = deployment.get("llm_secret_name")
    llm_env_keys = deployment.get("llm_env_keys") or []
    llm_provider_type = deployment.get("llm_provider_type")
    llm_provider_model = deployment.get("llm_provider_model")

    if llm_provider_type:
        env_vars.append(k8s_client.V1EnvVar(name="LLM_PROVIDER", value=llm_provider_type))
    if llm_provider_model:
        env_vars.append(k8s_client.V1EnvVar(name="LLM_MODEL", value=llm_provider_model))

    if llm_secret_name and llm_env_keys:
        for key in llm_env_keys:
            env_vars.append(
                k8s_client.V1EnvVar(
                    name=key,
                    value_from=k8s_client.V1EnvVarSource(
                        secret_key_ref=k8s_client.V1SecretKeySelector(
                            name=llm_secret_name,
                            key=key,
                        )
                    ),
                )
            )

    # --- WORKFLOW_JSON (declarative agents only) ---
    workflow_json_b64 = deployment.get("workflow_json_b64")
    if workflow_json_b64:
        env_vars.append(
            k8s_client.V1EnvVar(name="WORKFLOW_JSON", value=workflow_json_b64)
        )

    env_vars.append(
        k8s_client.V1EnvVar(name="REGISTRY_API_URL", value=registry_api_url)
    )

    python_executor_url = deployment.get(
        "python_executor_url",
        "http://agentshield-python-executor.agentshield-platform:8080",
    )
    env_vars.append(
        k8s_client.V1EnvVar(name="PYTHON_EXECUTOR_URL", value=python_executor_url)
    )

    # --- Agent container ---
    agent_container = k8s_client.V1Container(
        name=agent_name,
        image=image_tag,
        env=env_vars,
        volume_mounts=[
            # Phase 9.1: mount projected SA token for OPA identity check
            k8s_client.V1VolumeMount(
                name="sa-token",
                mount_path="/var/run/secrets/sa-token",
                read_only=True,
            )
        ],
        resources=k8s_client.V1ResourceRequirements(
            requests={"cpu": "100m", "memory": "256Mi"},
            limits={"cpu": "1000m", "memory": "1Gi"},
        ),
        liveness_probe=k8s_client.V1Probe(
            http_get=k8s_client.V1HTTPGetAction(path="/health", port=8080),
            initial_delay_seconds=15,
            period_seconds=10,
        ),
        readiness_probe=k8s_client.V1Probe(
            http_get=k8s_client.V1HTTPGetAction(path="/ready", port=8080),
            initial_delay_seconds=20,
            period_seconds=5,
        ),
    )

    # --- OPA sidecar (Phase 9.1: bundle server pull, not ConfigMap mount) ---
    opa_container = k8s_client.V1Container(
        name="opa",
        image=opa_image,
        args=[
            "run",
            "--server",
            "--addr=0.0.0.0:8181",
            "--config-file=/config/opa-config.yaml",
        ],
        ports=[k8s_client.V1ContainerPort(container_port=8181)],
        volume_mounts=[
            k8s_client.V1VolumeMount(
                name="opa-config",
                mount_path="/config",
                read_only=True,
            )
        ],
        resources=k8s_client.V1ResourceRequirements(
            requests={"cpu": "10m", "memory": "32Mi"},
            limits={"cpu": "100m", "memory": "128Mi"},
        ),
    )

    # --- Volumes ---
    volumes = [
        # Phase 9.1: projected bound SA token (audience=agentshield-opa, TTL=1h)
        k8s_client.V1Volume(
            name="sa-token",
            projected=k8s_client.V1ProjectedVolumeSource(
                sources=[
                    k8s_client.V1VolumeProjection(
                        service_account_token=k8s_client.V1ServiceAccountTokenProjection(
                            audience="agentshield-opa",
                            expiration_seconds=3600,
                            path="token",
                        )
                    )
                ]
            ),
        ),
        # Phase 9.1: OPA config for bundle server polling (shared ConfigMap)
        k8s_client.V1Volume(
            name="opa-config",
            config_map=k8s_client.V1ConfigMapVolumeSource(
                name=_OPA_CONFIG_CM,
            ),
        ),
    ]

    pod_template = k8s_client.V1PodTemplateSpec(
        metadata=k8s_client.V1ObjectMeta(labels=labels),
        spec=k8s_client.V1PodSpec(
            # Phase 9.1: run pod under the per-agent SA (provisions projected token)
            service_account_name=sa_name,
            containers=[agent_container, opa_container],
            volumes=volumes,
        ),
    )

    deployment_spec = k8s_client.V1DeploymentSpec(
        replicas=replicas,
        selector=k8s_client.V1LabelSelector(
            match_labels={"app.kubernetes.io/name": agent_name}
        ),
        template=pod_template,
    )

    return k8s_client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=k8s_client.V1ObjectMeta(
            name=k8s_name,
            namespace=namespace,
            labels=labels,
        ),
        spec=deployment_spec,
    )
