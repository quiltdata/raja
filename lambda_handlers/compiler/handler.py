"""Policy Compiler Lambda Handler.

Loads Cedar policies from AVP Policy Store, compiles them to scopes,
and stores the mappings in DynamoDB for token issuance.
"""

import json
import os
from typing import Any

import boto3
from raja import compile_policy

# Environment variables
POLICY_STORE_ID = os.environ["POLICY_STORE_ID"]
MAPPINGS_TABLE = os.environ["MAPPINGS_TABLE"]
PRINCIPAL_TABLE = os.environ["PRINCIPAL_TABLE"]

# AWS clients
avp_client = boto3.client("verifiedpermissions")
dynamodb = boto3.resource("dynamodb")
mappings_table = dynamodb.Table(MAPPINGS_TABLE)
principal_table = dynamodb.Table(PRINCIPAL_TABLE)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Compile Cedar policies from AVP to scopes and store in DynamoDB.

    Process:
    1. List all policies from AVP Policy Store
    2. For each policy, compile to scopes using raja.compile_policy
    3. Store policy_id → scopes mapping in PolicyScopeMappings table
    4. Aggregate all scopes by principal
    5. Store principal → scopes mapping in PrincipalScopes table

    Returns:
        Response with count of policies compiled
    """
    try:
        # List all policies from AVP
        policies_response = avp_client.list_policies(
            policyStoreId=POLICY_STORE_ID, maxResults=100
        )

        policies_compiled = 0
        principal_scopes: dict[str, set[str]] = {}

        # Process each policy
        for policy_item in policies_response.get("policies", []):
            policy_id = policy_item["policyId"]

            # Get full policy details
            policy_response = avp_client.get_policy(
                policyStoreId=POLICY_STORE_ID, policyId=policy_id
            )

            # Extract Cedar policy statement
            definition = policy_response.get("definition", {})
            static_def = definition.get("static", {})
            cedar_statement = static_def.get("statement", "")

            if not cedar_statement:
                continue

            # Compile policy to scopes
            principal_scope_map = compile_policy(cedar_statement)

            # Store policy → scopes mapping
            for principal, scopes in principal_scope_map.items():
                mappings_table.put_item(
                    Item={
                        "policy_id": policy_id,
                        "principal": principal,
                        "scopes": scopes,
                    }
                )

                # Aggregate scopes by principal
                if principal not in principal_scopes:
                    principal_scopes[principal] = set()
                principal_scopes[principal].update(scopes)

            policies_compiled += 1

        # Store aggregated principal → scopes mappings
        for principal, scopes in principal_scopes.items():
            principal_table.put_item(
                Item={
                    "principal": principal,
                    "scopes": list(scopes),
                }
            )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Policies compiled successfully",
                    "policies_compiled": policies_compiled,
                    "principals": len(principal_scopes),
                }
            ),
        }

    except Exception as e:
        print(f"Error compiling policies: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
