"""Backend stack for the arXiv Translator service.

Creates:
- ECR repository for the backend Docker image
- ECS Fargate cluster, task definition, and service
- Application Load Balancer with 300s idle timeout for SSE
- CloudWatch log group
- Proper security group configuration
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct


class BackendStack(Stack):
    """ECS Fargate service behind an ALB for the FastAPI backend."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        user_pool_id: str,
        user_pool_client_id: str,
        zip_bucket: s3.IBucket,
        cloudfront_domain: str,
        task_cpu: int = 512,
        task_memory_mib: int = 1024,
        desired_count: int = 1,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------------------------------------
        # ECR Repository
        # -----------------------------------------------------------
        self.ecr_repo = ecr.Repository(
            self,
            "BackendRepo",
            repository_name="arxiv-translator-backend",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 5 images",
                    max_image_count=5,
                    rule_priority=1,
                ),
            ],
        )

        Tags.of(self.ecr_repo).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # CloudWatch Log Group
        # -----------------------------------------------------------
        log_group = logs.LogGroup(
            self,
            "BackendLogGroup",
            log_group_name="/ecs/arxiv-translator-backend",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # -----------------------------------------------------------
        # ECS Cluster
        # -----------------------------------------------------------
        self.cluster = ecs.Cluster(
            self,
            "BackendCluster",
            cluster_name="arxiv-translator",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.DISABLED,
        )

        # -----------------------------------------------------------
        # Task Definition
        # -----------------------------------------------------------
        task_definition = ecs.FargateTaskDefinition(
            self,
            "BackendTaskDef",
            cpu=task_cpu,
            memory_limit_mib=task_memory_mib,
            family="arxiv-translator-backend",
        )

        # Grant S3 access to the task role
        zip_bucket.grant_read_write(task_definition.task_role)

        container = task_definition.add_container(
            "BackendContainer",
            image=ecs.ContainerImage.from_ecr_repository(self.ecr_repo, tag="latest"),
            container_name="arxiv-translator-backend",
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="backend",
                log_group=log_group,
            ),
            environment={
                "APP_ENV": "production",
                "LOG_LEVEL": "INFO",
                "AWS_REGION": Stack.of(self).region,
                "COGNITO_USER_POOL_ID": user_pool_id,
                "COGNITO_APP_CLIENT_ID": user_pool_client_id,
                "S3_BUCKET_NAME": zip_bucket.bucket_name,
                "CORS_ORIGINS": f"https://{cloudfront_domain}",
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/api/v1/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(15),
            ),
            port_mappings=[
                ecs.PortMapping(
                    container_port=8000,
                    protocol=ecs.Protocol.TCP,
                ),
            ],
        )

        # -----------------------------------------------------------
        # ALB Security Group
        # -----------------------------------------------------------
        alb_sg = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=vpc,
            description="Security group for arXiv Translator ALB",
            allow_all_outbound=True,
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP",
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS",
        )

        # -----------------------------------------------------------
        # ECS Service Security Group
        # -----------------------------------------------------------
        service_sg = ec2.SecurityGroup(
            self,
            "ServiceSecurityGroup",
            vpc=vpc,
            description="Security group for arXiv Translator ECS tasks",
            allow_all_outbound=True,
        )
        service_sg.add_ingress_rule(
            alb_sg,
            ec2.Port.tcp(8000),
            "Allow traffic from ALB",
        )

        # -----------------------------------------------------------
        # Application Load Balancer
        # -----------------------------------------------------------
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "BackendAlb",
            load_balancer_name="arxiv-translator-alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            idle_timeout=Duration.seconds(300),  # Required for SSE long connections
        )

        Tags.of(self.alb).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # Fargate Service
        # -----------------------------------------------------------
        self.service = ecs.FargateService(
            self,
            "BackendService",
            service_name="arxiv-translator-backend",
            cluster=self.cluster,
            task_definition=task_definition,
            desired_count=desired_count,
            assign_public_ip=False,
            security_groups=[service_sg],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            health_check_grace_period=Duration.seconds(60),
        )

        # -----------------------------------------------------------
        # ALB Target Group + Listener
        # -----------------------------------------------------------
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "BackendTargetGroup",
            target_group_name="arxiv-translator-tg",
            vpc=vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/api/v1/health",
                port="8000",
                protocol=elbv2.Protocol.HTTP,
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
            ),
            deregistration_delay=Duration.seconds(30),
        )

        target_group.add_target(self.service)

        # HTTP listener
        self.alb.add_listener(
            "HttpListener",
            port=80,
            default_target_groups=[target_group],
        )

        # -----------------------------------------------------------
        # Outputs
        # -----------------------------------------------------------
        CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.ecr_repo.repository_uri,
            description="ECR repository URI for backend image",
            export_name="ArxivTranslatorEcrUri",
        )
        CfnOutput(
            self,
            "AlbDnsName",
            value=self.alb.load_balancer_dns_name,
            description="ALB DNS name (backend API endpoint)",
            export_name="ArxivTranslatorAlbDns",
        )
        CfnOutput(
            self,
            "AlbUrl",
            value=f"http://{self.alb.load_balancer_dns_name}",
            description="Backend API base URL",
            export_name="ArxivTranslatorAlbUrl",
        )
        CfnOutput(
            self,
            "EcsClusterName",
            value=self.cluster.cluster_name,
            description="ECS cluster name",
        )
        CfnOutput(
            self,
            "EcsServiceName",
            value=self.service.service_name,
            description="ECS service name",
        )
