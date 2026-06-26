.PHONY: validate package apply reload

RULES_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
CONFIGMAP_NAME := nemo-yara-rules
NAMESPACE := agentshield-platform
NEMO_LABEL := app.kubernetes.io/name=nemo

validate:
	@echo "Validating YARA rules..."
	@yara --syntax-only $(RULES_DIR)default.yar /dev/null
	@echo "[OK] YARA syntax valid"

package: validate
	@echo "Packaging rules into ConfigMap YAML..."
	@kubectl create configmap $(CONFIGMAP_NAME) \
		--from-file=default.yar=$(RULES_DIR)default.yar \
		--namespace=$(NAMESPACE) \
		--dry-run=client -o yaml > /tmp/nemo-configmap.yaml
	@echo "[OK] ConfigMap YAML written to /tmp/nemo-configmap.yaml"

apply: package
	@echo "Applying ConfigMap to cluster..."
	@kubectl apply -f /tmp/nemo-configmap.yaml
	@echo "[OK] ConfigMap applied"

reload: apply
	@echo "Sending SIGHUP to NeMo pod for hot-reload..."
	@NEMO_POD=$$(kubectl get pod -n $(NAMESPACE) -l $(NEMO_LABEL) -o jsonpath='{.items[0].metadata.name}'); \
	kubectl exec -n $(NAMESPACE) $$NEMO_POD -- kill -HUP 1
	@echo "[OK] NeMo reloaded"
