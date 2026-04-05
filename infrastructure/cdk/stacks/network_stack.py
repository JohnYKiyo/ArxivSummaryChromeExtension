"""VPC network stack for the arXiv Translator service.

Creates a VPC with public and private subnets across 2 AZs,
with a single NAT Gateway to minimize cost.
"""

from aws_cdk import (
    CfnOutput,
    Stack,
    Tags,
    aws_ec2 as ec2,
)
from constructs import Construct


class NetworkStack(Stack):
    """VPC with public/private subnets and a single NAT Gateway."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------------------------------------
        # VPC
        # -----------------------------------------------------------
        self.vpc = ec2.Vpc(
            self,
            "ArxivTranslatorVpc",
            vpc_name="arxiv-translator-vpc",
            max_azs=2,
            nat_gateways=1,  # Single NAT GW for cost optimization
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        Tags.of(self.vpc).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # Outputs
        # -----------------------------------------------------------
        CfnOutput(self, "VpcId", value=self.vpc.vpc_id, description="VPC ID")
