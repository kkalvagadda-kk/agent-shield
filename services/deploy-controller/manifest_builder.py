import kubernetes.client as k8s_client


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
    Phase 9 update: change backendRef to safety-orchestrator.agentshield-platform:8080.
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
) -> k8s_client.V1Deployment:
    """
    Build a V1Deployment manifest for the given agent deployment record.

    Args:
        deployment: Deployment record from Registry API
        agent:      Agent record from GET /api/v1/agents/{name}
        version:    Version record from GET /api/v1/versions/{id}
        opa_image:  OPA sidecar image (e.g. openpolicyagent/opa:0.69.0-static)

    Returns:
        A kubernetes.client.V1Deployment ready to apply.
    """
    agent_name = agent["name"]
    environment = deployment["environment"]
    team = agent.get("team", "platform")
    namespace = deployment["k8s_namespace"]
    replicas = deployment.get("replicas", 1)

    k8s_name = f"{agent_name}-{environment}"
    image_tag = version["image_tag"]
    version_number = str(version.get("version_number", version.get("id", "unknown")))
    policy_cm_name = f"{agent_name}-policy"

    labels = {
        "app.kubernetes.io/name": agent_name,
        "app.kubernetes.io/version": version_number,
        "agentshield.io/team": team,
        "agentshield.io/environment": environment,
    }

    # --- Base env vars ---
    env_vars = [
        k8s_client.V1EnvVar(name="AGENT_NAME", value=agent_name),
        k8s_client.V1EnvVar(name="AGENT_VERSION", value=version_number),
        k8s_client.V1EnvVar(name="OPA_URL", value="http://localhost:8181"),
    ]

    # --- LLM provider env vars (populated by Registry API at deploy time) ---
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

    # --- REGISTRY_API_URL (declarative runner tool/skill resolution) ---
    env_vars.append(
        k8s_client.V1EnvVar(
            name="REGISTRY_API_URL",
            value_from=k8s_client.V1EnvVarSource(
                secret_key_ref=k8s_client.V1SecretKeySelector(
                    name="agentshield-secrets",
                    key="registry-api-url",
                )
            ),
        )
    )

    # --- Agent container ---
    agent_container = k8s_client.V1Container(
        name=agent_name,
        image=image_tag,
        env=env_vars,
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

    # --- OPA sidecar ---
    opa_container = k8s_client.V1Container(
        name="opa",
        image=opa_image,
        args=["run", "--server", "--addr=0.0.0.0:8181", "-b", "/policies/"],
        ports=[k8s_client.V1ContainerPort(container_port=8181)],
        volume_mounts=[
            k8s_client.V1VolumeMount(name="policy-bundle", mount_path="/policies")
        ],
        resources=k8s_client.V1ResourceRequirements(
            requests={"cpu": "10m", "memory": "32Mi"},
            limits={"cpu": "100m", "memory": "128Mi"},
        ),
    )

    # --- Policy bundle volume (ConfigMap) ---
    policy_volume = k8s_client.V1Volume(
        name="policy-bundle",
        config_map=k8s_client.V1ConfigMapVolumeSource(name=policy_cm_name),
    )

    pod_template = k8s_client.V1PodTemplateSpec(
        metadata=k8s_client.V1ObjectMeta(labels=labels),
        spec=k8s_client.V1PodSpec(
            containers=[agent_container, opa_container],
            volumes=[policy_volume],
        ),
    )

    deployment_spec = k8s_client.V1DeploymentSpec(
        replicas=replicas,
        selector=k8s_client.V1LabelSelector(match_labels={"app.kubernetes.io/name": agent_name}),
        template=pod_template,
    )

    manifest = k8s_client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=k8s_client.V1ObjectMeta(
            name=k8s_name,
            namespace=namespace,
            labels=labels,
        ),
        spec=deployment_spec,
    )

    return manifest
