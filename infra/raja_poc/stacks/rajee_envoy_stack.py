from __future__ import annotations

from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class RajeeEnvoyStack(Stack):
    """RAJEE S3 proxy using Envoy on ECS Fargate."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        jwt_signing_secret: secretsmanager.ISecret,
        certificate_arn: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        repo_root = Path(__file__).resolve().parents[3]
        asset_excludes = [
            ".git",
            ".venv",
            "infra/cdk.out",
            "infra/cdk.out/**",
            "infra/cdk.out.deploy",
            "infra/cdk.out.deploy/**",
        ]

        vpc = ec2.Vpc(
            self,
            "RajeeVpc",
            max_azs=2,
            nat_gateways=1,
        )

        cluster = ecs.Cluster(
            self,
            "RajeeCluster",
            vpc=vpc,
            container_insights=True,
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "RajeeTask",
            memory_limit_mib=512,
            cpu=256,
        )

        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                ],
                resources=["*"],
            )
        )

        if task_definition.execution_role is not None:
            jwt_signing_secret.grant_read(task_definition.execution_role)

        envoy_container = task_definition.add_container(
            "EnvoyProxy",
            image=ecs.ContainerImage.from_asset(
                str(repo_root),
                file="infra/raja_poc/assets/envoy/Dockerfile",
                exclude=asset_excludes,
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="envoy"),
            environment={"ENVOY_LOG_LEVEL": "info"},
        )
        envoy_container.add_port_mappings(
            ecs.PortMapping(container_port=10000, protocol=ecs.Protocol.TCP),
            ecs.PortMapping(container_port=9901, protocol=ecs.Protocol.TCP),
        )

        authorizer_container = task_definition.add_container(
            "Authorizer",
            image=ecs.ContainerImage.from_asset(
                str(repo_root),
                file="lambda_handlers/authorizer/Dockerfile",
                exclude=asset_excludes,
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="authorizer"),
            secrets={
                "JWT_SECRET": ecs.Secret.from_secrets_manager(jwt_signing_secret),
            },
        )
        authorizer_container.add_port_mappings(
            ecs.PortMapping(container_port=9000, protocol=ecs.Protocol.TCP)
        )

        certificate = None
        protocol = elbv2.ApplicationProtocol.HTTP
        listener_port = 80
        if certificate_arn:
            certificate = acm.Certificate.from_certificate_arn(
                self, "RajeeCertificate", certificate_arn
            )
            protocol = elbv2.ApplicationProtocol.HTTPS
            listener_port = 443

        alb_kwargs: dict[str, object] = {
            "cluster": cluster,
            "task_definition": task_definition,
            "desired_count": 2,
            "public_load_balancer": True,
            "listener_port": listener_port,
            "protocol": protocol,
        }
        if certificate is not None:
            alb_kwargs["certificate"] = certificate

        alb_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "RajeeService",
            **alb_kwargs,
        )

        alb_service.target_group.configure_health_check(
            path="/ready",
            port="9901",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        scaling = alb_service.service.auto_scale_task_count(
            min_capacity=2,
            max_capacity=10,
        )

        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
        )

        scaling.scale_on_request_count(
            "RequestScaling",
            requests_per_target=1000,
            target_group=alb_service.target_group,
        )

        self.load_balancer = alb_service.load_balancer
        self.service = alb_service.service
