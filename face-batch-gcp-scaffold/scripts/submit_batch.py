#!/usr/bin/env python3
"""Submit one historical-video processing job to Google Cloud Batch.

This is an integration scaffold, not the worker itself.

Prerequisites:
  pip install google-cloud-batch

The caller needs:
  roles/batch.jobsEditor on the project
  roles/iam.serviceAccountUser on the worker service account

Example:
  python submit_batch.py \
    --project my-project \
    --region us-central1 \
    --job-id face-20260826-001 \
    --image us-central1-docker.pkg.dev/my-project/face-batch-worker/worker:latest \
    --service-account face-batch-worker@my-project.iam.gserviceaccount.com \
    --network projects/my-project/global/networks/face-batch-vpc \
    --subnetwork projects/my-project/regions/us-central1/subnetworks/face-batch-batch \
    --gcs-uri gs://my-project-face-staging/incoming/clip.mp4 \
    --source-ref archive://camera17/2026-03-14/segment-0083 \
    --sha256 abcdef...
"""

from __future__ import annotations

import argparse
from google.cloud import batch_v1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--service-account", required=True)
    p.add_argument("--network", required=True)
    p.add_argument("--subnetwork", required=True)
    p.add_argument("--gcs-uri", required=True)
    p.add_argument("--source-ref", required=True)
    p.add_argument("--sha256")
    p.add_argument("--machine-type", default="g2-standard-8")
    p.add_argument("--max-run-seconds", type=int, default=21600)
    p.add_argument("--on-demand", action="store_true", help="Use STANDARD instead of SPOT provisioning")
    return p.parse_args()


def build_job(a: argparse.Namespace) -> batch_v1.Job:
    commands = [
        "--gcs-uri", a.gcs_uri,
        "--external-source-ref", a.source_ref,
        "--job-id", a.job_id,
    ]
    if a.sha256:
        commands.extend(["--expected-sha256", a.sha256])

    runnable = batch_v1.Runnable(
        container=batch_v1.Runnable.Container(
            image_uri=a.image,
            commands=commands,
        )
    )

    task = batch_v1.TaskSpec(
        runnables=[runnable],
        max_retry_count=2,
        max_run_duration=f"{a.max_run_seconds}s",
    )

    group = batch_v1.TaskGroup(task_count=1, parallelism=1, task_spec=task)

    policy = batch_v1.AllocationPolicy.InstancePolicy(machine_type=a.machine_type)
    if not a.on_demand:
        policy.provisioning_model = batch_v1.AllocationPolicy.ProvisioningModel.SPOT

    instance = batch_v1.AllocationPolicy.InstancePolicyOrTemplate(
        policy=policy,
        install_gpu_drivers=True,
    )

    network = batch_v1.AllocationPolicy.NetworkPolicy(
        network_interfaces=[
            batch_v1.AllocationPolicy.NetworkInterface(
                network=a.network,
                subnetwork=a.subnetwork,
                # MVP leaves the VM with an ephemeral external IP because
                # Batch-managed GPU-driver installation fetches drivers at runtime.
                # The custom VPC has no ingress firewall rules. Once a custom VM
                # image with compatible GPU drivers is used, harden this to True.
                no_external_ip_address=False,
            )
        ]
    )

    allocation = batch_v1.AllocationPolicy(
        instances=[instance],
        network=network,
        service_account=batch_v1.ServiceAccount(email=a.service_account),
    )

    return batch_v1.Job(
        task_groups=[group],
        allocation_policy=allocation,
        logs_policy=batch_v1.LogsPolicy(destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING),
        labels={"workload": "face-batch", "data": "biometric"},
    )


def main() -> None:
    a = parse_args()
    client = batch_v1.BatchServiceClient()
    request = batch_v1.CreateJobRequest(
        parent=f"projects/{a.project}/locations/{a.region}",
        job_id=a.job_id,
        job=build_job(a),
    )
    created = client.create_job(request=request)
    print(created.name)


if __name__ == "__main__":
    main()
