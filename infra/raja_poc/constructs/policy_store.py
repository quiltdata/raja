from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from aws_cdk import aws_verifiedpermissions as verifiedpermissions
from constructs import Construct


class PolicyStore(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        schema: str,
        policies: Mapping[str, str],
    ) -> None:
        super().__init__(scope, construct_id)

        # Dynamically parse Cedar schema to AVP JSON format
        # This eliminates hardcoded schema drift risk
        try:
            # Import here to avoid circular dependencies and keep CDK dependencies separate
            import sys

            # Add src directory to path to import raja modules
            repo_root = Path(__file__).parent.parent.parent.parent
            sys.path.insert(0, str(repo_root / "src"))

            from raja.cedar import parse_cedar_schema_to_avp_json

            # Parse the Cedar schema text to AVP-compatible JSON
            schema_json = parse_cedar_schema_to_avp_json(schema, namespace="Raja")

        except Exception as e:
            # Fail fast with clear error message if schema parsing fails
            raise ValueError(f"Failed to parse Cedar schema: {e}") from e

        policy_store = verifiedpermissions.CfnPolicyStore(
            self,
            "PolicyStore",
            validation_settings=verifiedpermissions.CfnPolicyStore.ValidationSettingsProperty(
                mode="STRICT"
            ),
            schema=verifiedpermissions.CfnPolicyStore.SchemaDefinitionProperty(
                cedar_json=schema_json
            ),
        )

        for name, statement in policies.items():
            verifiedpermissions.CfnPolicy(
                self,
                f"Policy{name}",
                policy_store_id=policy_store.attr_policy_store_id,
                definition=verifiedpermissions.CfnPolicy.PolicyDefinitionProperty(
                    static=verifiedpermissions.CfnPolicy.StaticPolicyDefinitionProperty(
                        statement=statement
                    )
                ),
            )

        self.policy_store_id = policy_store.attr_policy_store_id
        self.policy_store_arn = policy_store.attr_arn
