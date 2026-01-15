from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, CfnParameter, Duration, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

from ..utils.platform import detect_platform, get_platform_string


class RajeeEnvoyStack(Stack):
    """RAJEE S3 proxy using Envoy on ECS Fargate."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        certificate_arn: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Detect platform at synth time
        ecs_arch, docker_platform, _ = detect_platform()
        print(f"[RAJEE] Deploying for platform: {get_platform_string()}")

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
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs_arch,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        test_bucket = s3.Bucket(
            self,
            "RajeeTestBucket",
            bucket_name=f"raja-poc-test-{Stack.of(self).account}-{Stack.of(self).region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
        )

        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                resources=[
                    test_bucket.bucket_arn,
                    f"{test_bucket.bucket_arn}/*",
                ],
            )
        )

        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[test_bucket.bucket_arn],
            )
        )

        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "RAJEE"}},
            )
        )

        auth_disabled = CfnParameter(
            self,
            "AUTH_DISABLED",
            type="String",
            default="true",
            allowed_values=["true", "false"],
            description="Disable authorization checks in Envoy (fail-open for bootstrap).",
        )

        envoy_container = task_definition.add_container(
            "EnvoyProxy",
            image=ecs.ContainerImage.from_asset(
                str(repo_root),
                file="infra/raja_poc/assets/envoy/Dockerfile",
                exclude=asset_excludes,
                platform=docker_platform,
            ),
            cpu=128,
            memory_limit_mib=256,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="envoy"),
            environment={
                "ENVOY_LOG_LEVEL": "info",
                "AUTH_DISABLED": auth_disabled.value_as_string,
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:9901/ready || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        envoy_container.add_port_mappings(
            ecs.PortMapping(container_port=10000, protocol=ecs.Protocol.TCP),
            ecs.PortMapping(container_port=9901, protocol=ecs.Protocol.TCP),
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

        # Allow ALB to reach Envoy admin port for health checks
        alb_service.service.connections.allow_from(
            alb_service.load_balancer,
            ec2.Port.tcp(9901),
            "Allow ALB health checks to Envoy admin port",
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

        dashboard = cloudwatch.Dashboard(
            self,
            "RajeeDashboard",
            dashboard_name="RAJEE-Monitoring",
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Authorization Decisions",
                left=[
                    cloudwatch.Metric(
                        namespace="RAJEE",
                        metric_name="AuthorizationAllow",
                        statistic="Sum",
                    ),
                    cloudwatch.Metric(
                        namespace="RAJEE",
                        metric_name="AuthorizationDeny",
                        statistic="Sum",
                    ),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Authorization Latency",
                left=[
                    cloudwatch.Metric(
                        namespace="RAJEE",
                        metric_name="AuthorizationLatency",
                        statistic="Average",
                    ),
                ],
            ),
        )

        CfnOutput(
            self,
            "DeploymentPlatform",
            value=get_platform_string(),
            description="Platform architecture used for this deployment",
        )
        CfnOutput(
            self,
            "TestBucketName",
            value=test_bucket.bucket_name,
            description="S3 bucket for RAJEE proxy testing",
        )

        self.load_balancer = alb_service.load_balancer
        self.service = alb_service.service
