"""Cognito authentication stack for the arXiv Translator service.

Creates a User Pool with email-based sign-up, a web app client,
and a hosted-UI domain.
"""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    Tags,
    aws_cognito as cognito,
)
from constructs import Construct


class AuthStack(Stack):
    """Amazon Cognito User Pool for user authentication."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------------------------------------
        # User Pool
        # -----------------------------------------------------------
        self.user_pool = cognito.UserPool(
            self,
            "ArxivTranslatorUserPool",
            user_pool_name="arxiv-translator-users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
        )

        Tags.of(self.user_pool).add("Project", "arxiv-translator")

        # -----------------------------------------------------------
        # User Pool Client (web app - public client, no secret)
        # -----------------------------------------------------------
        self.user_pool_client = self.user_pool.add_client(
            "ArxivTranslatorWebClient",
            user_pool_client_name="arxiv-translator-web",
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                    implicit_code_grant=True,
                ),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                callback_urls=["http://localhost:5173/callback", "https://localhost/callback"],
                logout_urls=["http://localhost:5173", "https://localhost"],
            ),
            generate_secret=False,
            prevent_user_existence_errors=True,
        )

        # -----------------------------------------------------------
        # Hosted UI Domain
        # -----------------------------------------------------------
        self.user_pool_domain = self.user_pool.add_domain(
            "ArxivTranslatorDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix="arxiv-translator",
            ),
        )

        # -----------------------------------------------------------
        # Outputs
        # -----------------------------------------------------------
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
            export_name="ArxivTranslatorUserPoolId",
        )
        CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito User Pool Client ID",
            export_name="ArxivTranslatorUserPoolClientId",
        )
        CfnOutput(
            self,
            "UserPoolDomain",
            value=self.user_pool_domain.domain_name,
            description="Cognito Hosted UI Domain prefix",
            export_name="ArxivTranslatorUserPoolDomain",
        )
