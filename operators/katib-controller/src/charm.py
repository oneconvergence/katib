#!/usr/bin/env python3

import logging
from pathlib import Path
from subprocess import check_call

import yaml
from oci_image import OCIImageResource, OCIImageResourceError
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus

logger = logging.getLogger(__name__)


class Operator(CharmBase):
    """Deploys the katib-controller service."""

    _stored = StoredState()

    def __init__(self, framework):
        super().__init__(framework)

        if not self.model.unit.is_leader():
            logger.info("Not a leader, skipping any work")
            self.model.unit.status = ActiveStatus()
            return

        self._stored.set_default(**self.gen_certs())
        self.image = OCIImageResource(self, "oci-image")
        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)

    def set_pod_spec(self, event):
        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            self.model.unit.status = e.status
            return

        validating, mutating = yaml.safe_load_all(Path("src/webhooks.yaml").read_text())

        self.model.pod.set_spec(
            {
                "version": 3,
                "serviceAccount": {
                    "roles": [
                        {
                            "global": True,
                            "rules": [
                                {
                                    "apiGroups": [""],
                                    "resources": [
                                        "configmaps",
                                        "serviceaccounts",
                                        "services",
                                        "events",
                                        "namespaces",
                                        "persistentvolumes",
                                        "persistentvolumeclaims",
                                        "pods",
                                        "pods/log",
                                        "pods/status",
                                    ],
                                    "verbs": ["*"],
                                },
                                {
                                    "apiGroups": ["apps"],
                                    "resources": ["deployments"],
                                    "verbs": ["*"],
                                },
                                {
                                    "apiGroups": ["rbac.authorization.k8s.io"],
                                    "resources": [
                                        "roles",
                                        "rolebindings",
                                    ],
                                    "verbs": ["*"],
                                },
                                {
                                    "apiGroups": ["batch"],
                                    "resources": ["jobs", "cronjobs"],
                                    "verbs": ["*"],
                                },
                                {
                                    "apiGroups": ["kubeflow.org"],
                                    "resources": [
                                        "experiments",
                                        "experiments/status",
                                        "experiments/finalizers",
                                        "trials",
                                        "trials/status",
                                        "trials/finalizers",
                                        "suggestions",
                                        "suggestions/status",
                                        "suggestions/finalizers",
                                        "tfjobs",
                                        "pytorchjobs",
                                        "mpijobs",
                                        "xgboostjobs",
                                        "mxjobs",
                                    ],
                                    "verbs": ["*"],
                                },
                            ],
                        }
                    ],
                },
                "containers": [
                    {
                        "name": "katib-controller",
                        "imageDetails": image_details,
                        "command": ["./katib-controller"],
                        "args": [
                            f"--webhook-port={self.model.config['webhook-port']}",
                            "--trial-resources=Job.v1.batch",
                            "--trial-resources=TFJob.v1.kubeflow.org",
                            "--trial-resources=PyTorchJob.v1.kubeflow.org",
                            "--trial-resources=MPIJob.v1.kubeflow.org",
                            "--trial-resources=PipelineRun.v1beta1.tekton.dev",
                        ],
                        "ports": [
                            {
                                "name": "webhook",
                                "containerPort": self.model.config["webhook-port"],
                            },
                            {
                                "name": "metrics",
                                "containerPort": self.model.config["metrics-port"],
                            },
                        ],
                        "envConfig": {
                            "KATIB_CORE_NAMESPACE": self.model.name,
                        },
                        "volumeConfig": [
                            {
                                "name": "certs",
                                "mountPath": "/tmp/cert",
                                "files": [
                                    {
                                        "path": "tls.crt",
                                        "content": self._stored.cert,
                                    },
                                    {
                                        "path": "tls.key",
                                        "content": self._stored.key,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
            k8s_resources={
                "kubernetesResources": {
                    "customResourceDefinitions": [
                        {"name": crd["metadata"]["name"], "spec": crd["spec"]}
                        for crd in yaml.safe_load_all(Path("src/crds.yaml").read_text())
                    ],
                    "mutatingWebhookConfigurations": [
                        {
                            "name": mutating["metadata"]["name"],
                            "webhooks": mutating["webhooks"],
                        }
                    ],
                    "validatingWebhookConfigurations": [
                        {
                            "name": validating["metadata"]["name"],
                            "webhooks": validating["webhooks"],
                        }
                    ],
                },
                "configMaps": {
                    "katib-config": {
                        f: Path(f"src/{f}.json").read_text()
                        for f in (
                            "metrics-collector-sidecar",
                            "suggestion",
                            "early-stopping",
                        )
                    },
                    "trial-template": {
                        f + suffix: Path(f"src/{f}.yaml").read_text()
                        for f, suffix in (
                            ("defaultTrialTemplate", ".yaml"),
                            ("enasCPUTemplate", ""),
                            ("pytorchJobTemplate", ""),
                        )
                    },
                },
            },
        )

        self.model.unit.status = ActiveStatus()

    def gen_certs(self):
        model = self.model.name
        app = self.model.app.name
        Path("/run/ssl.conf").write_text(
            f"""[ req ]
default_bits = 2048
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn
[ dn ]
C = GB
ST = Canonical
L = Canonical
O = Canonical
OU = Canonical
CN = 127.0.0.1
[ req_ext ]
subjectAltName = @alt_names
[ alt_names ]
DNS.1 = {app}
DNS.2 = {app}.{model}
DNS.3 = {app}.{model}.svc
DNS.4 = {app}.{model}.svc.cluster
DNS.5 = {app}.{model}.svc.cluster.local
IP.1 = 127.0.0.1
[ v3_ext ]
authorityKeyIdentifier=keyid,issuer:always
basicConstraints=CA:FALSE
keyUsage=keyEncipherment,dataEncipherment,digitalSignature
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names"""
        )

        check_call(["openssl", "genrsa", "-out", "/run/ca.key", "2048"])
        check_call(["openssl", "genrsa", "-out", "/run/server.key", "2048"])
        check_call(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-sha256",
                "-nodes",
                "-days",
                "3650",
                "-key",
                "/run/ca.key",
                "-subj",
                "/CN=127.0.0.1",
                "-out",
                "/run/ca.crt",
            ]
        )
        check_call(
            [
                "openssl",
                "req",
                "-new",
                "-sha256",
                "-key",
                "/run/server.key",
                "-out",
                "/run/server.csr",
                "-config",
                "/run/ssl.conf",
            ]
        )
        check_call(
            [
                "openssl",
                "x509",
                "-req",
                "-sha256",
                "-in",
                "/run/server.csr",
                "-CA",
                "/run/ca.crt",
                "-CAkey",
                "/run/ca.key",
                "-CAcreateserial",
                "-out",
                "/run/cert.pem",
                "-days",
                "365",
                "-extensions",
                "v3_ext",
                "-extfile",
                "/run/ssl.conf",
            ]
        )

        return {
            "cert": Path("/run/cert.pem").read_text(),
            "key": Path("/run/server.key").read_text(),
            "ca": Path("/run/ca.crt").read_text(),
        }


if __name__ == "__main__":
    main(Operator)
