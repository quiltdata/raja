from __future__ import annotations

import json
from collections.abc import Mapping

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

        # Convert Cedar schema to JSON format expected by AVP
        # The schema should be a JSON object with entity types and action definitions
        schema_json = json.dumps(
            {
                "Raja": {
                    "entityTypes": {"User": {}, "Document": {}},
                    "actions": {
                        "read": {
                            "appliesTo": {"principalTypes": ["User"], "resourceTypes": ["Document"]}
                        },
                        "write": {
                            "appliesTo": {"principalTypes": ["User"], "resourceTypes": ["Document"]}
                        },
                        "delete": {
                            "appliesTo": {"principalTypes": ["User"], "resourceTypes": ["Document"]}
                        },
                    },
                }
            }
        )

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
