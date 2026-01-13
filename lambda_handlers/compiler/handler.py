from __future__ import annotations

import json
import os
from typing import Any

import boto3

from raja import compile_policy


def _load_policies(policy_store_id: str) -> list[dict[str, Any]]:
    client = boto3.client("verifiedpermissions")
    policies: list[dict[str, Any]] = []

    response = client.list_policies(policyStoreId=policy_store_id)
    policies.extend(response.get("policies", []))

    for policy_meta in policies:
        policy_id = policy_meta.get("policyId")
        if not policy_id:
            continue
        policy = client.get_policy(policyStoreId=policy_store_id, policyId=policy_id)
        policy_meta["statement"] = (
            policy.get("policy", {}).get("definition", {}).get("static", {}).get("statement", "")
        )

    return policies


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    policy_store_id = os.environ.get("POLICY_STORE_ID", "")
    mappings_table_name = os.environ.get("MAPPINGS_TABLE", "")
    principal_table_name = os.environ.get("PRINCIPAL_TABLE", "")

    if not policy_store_id or not mappings_table_name or not principal_table_name:
        return {"statusCode": 500, "body": "Missing configuration"}

    policies = _load_policies(policy_store_id)
    dynamodb = boto3.resource("dynamodb")
    mappings_table = dynamodb.Table(mappings_table_name)
    principal_table = dynamodb.Table(principal_table_name)

    principal_scopes: dict[str, list[str]] = {}

    for policy in policies:
        policy_id = policy.get("policyId", "")
        statement = policy.get("statement", "")
        if not policy_id or not statement:
            continue

        compiled = compile_policy(statement)
        scopes = list(next(iter(compiled.values()), []))
        mappings_table.put_item(
            Item={
                "policy_id": policy_id,
                "scopes": scopes,
            }
        )

        for principal, scopes in compiled.items():
            principal_scopes.setdefault(principal, [])
            for scope in scopes:
                if scope not in principal_scopes[principal]:
                    principal_scopes[principal].append(scope)

    for principal, scopes in principal_scopes.items():
        principal_table.put_item(
            Item={
                "principal": principal,
                "scopes": scopes,
            }
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"policies_compiled": len(policies)}),
    }
