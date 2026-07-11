import logging

import kubernetes
from kubernetes import client as k8s_client
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)


class K8sClient:
    def __init__(self):
        try:
            kubernetes.config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except kubernetes.config.ConfigException:
            kubernetes.config.load_kube_config()
            logger.info("Loaded local kubeconfig")

        self.apps_v1 = k8s_client.AppsV1Api()
        self.core_v1 = k8s_client.CoreV1Api()
        self.custom = k8s_client.CustomObjectsApi()

    def ensure_namespace(self, namespace: str) -> None:
        """Create namespace if it doesn't exist."""
        try:
            self.core_v1.read_namespace(name=namespace)
        except ApiException as e:
            if e.status != 404:
                raise
            ns = k8s_client.V1Namespace(
                metadata=k8s_client.V1ObjectMeta(
                    name=namespace,
                    labels={"agentshield.io/managed-by": "deploy-controller"},
                )
            )
            self.core_v1.create_namespace(body=ns)
            logger.info("Created namespace %s", namespace)

    def get_deployment(self, namespace: str, name: str) -> k8s_client.V1Deployment | None:
        """Return the named Deployment or None if it doesn't exist."""
        try:
            return self.apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def create_or_update_deployment(
        self, namespace: str, manifest: k8s_client.V1Deployment
    ) -> k8s_client.V1Deployment:
        """Create the Deployment if it doesn't exist; patch it if it does."""
        name = manifest.metadata.name
        existing = self.get_deployment(namespace, name)
        if existing is None:
            logger.info("Creating Deployment %s/%s", namespace, name)
            return self.apps_v1.create_namespaced_deployment(
                namespace=namespace, body=manifest
            )
        else:
            logger.info("Patching Deployment %s/%s", namespace, name)
            return self.apps_v1.patch_namespaced_deployment(
                name=name, namespace=namespace, body=manifest
            )

    def delete_deployment(self, namespace: str, name: str) -> None:
        """Gracefully delete a Deployment (foreground propagation)."""
        try:
            self.apps_v1.delete_namespaced_deployment(
                name=name,
                namespace=namespace,
                body=k8s_client.V1DeleteOptions(
                    propagation_policy="Foreground",
                    grace_period_seconds=30,
                ),
            )
            logger.info("Deleted Deployment %s/%s", namespace, name)
        except ApiException as e:
            if e.status == 404:
                logger.warning("Deployment %s/%s not found; skipping delete", namespace, name)
            else:
                raise

    def create_configmap_if_missing(
        self, namespace: str, name: str, data: dict[str, str]
    ) -> None:
        """Create a ConfigMap only if one does not already exist."""
        try:
            self.core_v1.read_namespaced_config_map(name=name, namespace=namespace)
            logger.debug("ConfigMap %s/%s already exists", namespace, name)
        except ApiException as e:
            if e.status != 404:
                raise
            cm = k8s_client.V1ConfigMap(
                metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
                data=data,
            )
            self.core_v1.create_namespaced_config_map(namespace=namespace, body=cm)
            logger.info("Created ConfigMap %s/%s", namespace, name)

    def ensure_secret(self, name: str, namespace: str, data: dict) -> None:
        """Create or update an Opaque secret with the given string data."""
        from kubernetes.client import V1Secret, V1ObjectMeta
        secret = V1Secret(
            metadata=V1ObjectMeta(name=name, namespace=namespace),
            string_data=data,
            type="Opaque",
        )
        try:
            self.core_v1.read_namespaced_secret(name=name, namespace=namespace)
            self.core_v1.replace_namespaced_secret(name=name, namespace=namespace, body=secret)
            logger.info("Updated Secret %s/%s", namespace, name)
        except ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_secret(namespace=namespace, body=secret)
                logger.info("Created Secret %s/%s", namespace, name)
            else:
                raise

    def copy_secret(self, name: str, src_namespace: str, dst_namespace: str) -> None:
        """Copy a Secret from src_namespace to dst_namespace (create or update)."""
        from kubernetes.client import V1Secret, V1ObjectMeta
        try:
            src = self.core_v1.read_namespaced_secret(name=name, namespace=src_namespace)
        except ApiException as e:
            if e.status == 404:
                logger.warning("Secret %s/%s not found, skipping copy", src_namespace, name)
                return
            raise
        secret = V1Secret(
            metadata=V1ObjectMeta(name=name, namespace=dst_namespace),
            data=src.data,
            type=src.type or "Opaque",
        )
        try:
            self.core_v1.read_namespaced_secret(name=name, namespace=dst_namespace)
            self.core_v1.replace_namespaced_secret(name=name, namespace=dst_namespace, body=secret)
            logger.info("Updated Secret copy %s/%s (from %s)", dst_namespace, name, src_namespace)
        except ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_secret(namespace=dst_namespace, body=secret)
                logger.info("Copied Secret %s/%s -> %s", src_namespace, name, dst_namespace)
            else:
                raise

    def scale_deployment(self, namespace: str, name: str, replicas: int) -> None:
        """Scale a Deployment to the specified replica count."""
        try:
            self.apps_v1.patch_namespaced_deployment_scale(
                name=name,
                namespace=namespace,
                body={"spec": {"replicas": replicas}},
            )
            logger.info("Scaled Deployment %s/%s to %d replicas", namespace, name, replicas)
        except ApiException as e:
            if e.status == 404:
                logger.warning("Deployment %s/%s not found; skipping scale", namespace, name)
            else:
                raise

    def delete_service(self, namespace: str, name: str) -> None:
        """Delete a Service (ignore if not found)."""
        try:
            self.core_v1.delete_namespaced_service(name=name, namespace=namespace)
            logger.info("Deleted Service %s/%s", namespace, name)
        except ApiException as e:
            if e.status == 404:
                logger.warning("Service %s/%s not found; skipping delete", namespace, name)
            else:
                raise

    def get_deployment_available_replicas(self, namespace: str, name: str) -> int:
        """Return the number of available replicas for a Deployment (0 if not found)."""
        deployment = self.get_deployment(namespace, name)
        if deployment is None or deployment.status is None:
            return 0
        return deployment.status.available_replicas or 0

    def create_or_update_service(
        self, namespace: str, manifest: k8s_client.V1Service
    ) -> k8s_client.V1Service:
        """Create or patch a ClusterIP Service for an agent pod."""
        name = manifest.metadata.name
        try:
            existing = self.core_v1.read_namespaced_service(name=name, namespace=namespace)
            # Preserve the clusterIP assigned by Kubernetes on the patch.
            manifest.spec.cluster_ip = existing.spec.cluster_ip
            logger.info("Patching Service %s/%s", namespace, name)
            return self.core_v1.patch_namespaced_service(
                name=name, namespace=namespace, body=manifest
            )
        except ApiException as e:
            if e.status != 404:
                raise
            logger.info("Creating Service %s/%s", namespace, name)
            return self.core_v1.create_namespaced_service(
                namespace=namespace, body=manifest
            )

    def apply_httproute(self, namespace: str, manifest: dict) -> None:
        """Create or replace a gateway.networking.k8s.io/v1 HTTPRoute custom resource."""
        name = manifest["metadata"]["name"]
        group = "gateway.networking.k8s.io"
        version = "v1"
        plural = "httproutes"
        try:
            self.custom.get_namespaced_custom_object(
                group=group, version=version, namespace=namespace,
                plural=plural, name=name,
            )
            logger.info("Replacing HTTPRoute %s/%s", namespace, name)
            self.custom.replace_namespaced_custom_object(
                group=group, version=version, namespace=namespace,
                plural=plural, name=name, body=manifest,
            )
        except ApiException as e:
            if e.status != 404:
                raise
            logger.info("Creating HTTPRoute %s/%s", namespace, name)
            self.custom.create_namespaced_custom_object(
                group=group, version=version, namespace=namespace,
                plural=plural, body=manifest,
            )

    def delete_httproute(self, namespace: str, name: str) -> None:
        """Delete a gateway HTTPRoute (called on agent termination)."""
        try:
            self.custom.delete_namespaced_custom_object(
                group="gateway.networking.k8s.io", version="v1",
                namespace=namespace, plural="httproutes", name=name,
            )
            logger.info("Deleted HTTPRoute %s/%s", namespace, name)
        except ApiException as e:
            if e.status == 404:
                logger.warning("HTTPRoute %s/%s not found; skipping delete", namespace, name)
            else:
                raise

    def ensure_service_account(self, agent_name: str, namespace: str) -> str:
        """Idempotently create a per-agent K8s ServiceAccount.

        Returns the full SA subject string:
            system:serviceaccount:{namespace}:{sa_name}
        """
        sa_name = f"agent-{agent_name}-sa"
        try:
            self.core_v1.read_namespaced_service_account(name=sa_name, namespace=namespace)
            logger.debug("ServiceAccount %s/%s already exists", namespace, sa_name)
        except ApiException as e:
            if e.status != 404:
                raise
            sa = k8s_client.V1ServiceAccount(
                metadata=k8s_client.V1ObjectMeta(
                    name=sa_name,
                    namespace=namespace,
                    labels={
                        "agentshield.io/agent-name": agent_name,
                        "agentshield.io/managed-by": "deploy-controller",
                    },
                )
            )
            self.core_v1.create_namespaced_service_account(namespace=namespace, body=sa)
            logger.info("Created ServiceAccount %s/%s", namespace, sa_name)

        return f"system:serviceaccount:{namespace}:{sa_name}"

    def ensure_opa_configmap(self, namespace: str) -> None:
        """Idempotently create the opa-sidecar-config ConfigMap in a namespace."""
        cm_name = "opa-sidecar-config"
        try:
            self.core_v1.read_namespaced_config_map(name=cm_name, namespace=namespace)
            logger.debug("ConfigMap %s/%s already exists", namespace, cm_name)
            return
        except ApiException as e:
            if e.status != 404:
                raise

        opa_yaml = (
            "services:\n"
            "  - name: bundle-server\n"
            "    url: http://opa-bundle-server.agentshield-platform\n"
            "\n"
            "bundles:\n"
            "  agentshield:\n"
            "    service: bundle-server\n"
            "    resource: /bundles/agentshield\n"
            "    polling:\n"
            "      min_delay_seconds: 5\n"
            "      max_delay_seconds: 15\n"
            "\n"
            "decision_logs:\n"
            "  console: true\n"
        )
        cm = k8s_client.V1ConfigMap(
            metadata=k8s_client.V1ObjectMeta(
                name=cm_name,
                namespace=namespace,
                labels={"agentshield.io/managed-by": "deploy-controller"},
            ),
            data={"opa-config.yaml": opa_yaml},
        )
        self.core_v1.create_namespaced_config_map(namespace=namespace, body=cm)
        logger.info("Created ConfigMap %s/%s", namespace, cm_name)

    def patch_configmap_data(
        self, namespace: str, name: str, key: str, value: str
    ) -> None:
        """Patch a single key in a ConfigMap's data field (used by bundle generator)."""
        patch = {"data": {key: value}}
        self.core_v1.patch_namespaced_config_map(name=name, namespace=namespace, body=patch)
        logger.info("Patched ConfigMap %s/%s key='%s'", namespace, name, key)
