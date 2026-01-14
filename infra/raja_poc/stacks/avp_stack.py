from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Stack
from constructs import Construct

from ..constructs.policy_store import PolicyStore


class AvpStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        repo_root = Path(__file__).resolve().parents[3]
        schema_path = repo_root / "policies" / "schema.cedar"
        policies_dir = repo_root / "policies" / "policies"

        schema = schema_path.read_text(encoding="utf-8")
        policies = {
            path.stem: path.read_text(encoding="utf-8")
            for path in sorted(policies_dir.glob("*.cedar"))
        }

        policy_store = PolicyStore(self, "PolicyStore", schema=schema, policies=policies)

        self.policy_store_id = policy_store.policy_store_id
        self.policy_store_arn = policy_store.policy_store_arn

        CfnOutput(self, "PolicyStoreId", value=self.policy_store_id)
        CfnOutput(self, "PolicyStoreArn", value=self.policy_store_arn)
