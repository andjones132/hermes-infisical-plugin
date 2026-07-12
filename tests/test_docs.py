"""Task #26 doc test: README documents exactly the keys config_schema()
declares (constitution: documentation matches implementation)."""

import re
from pathlib import Path

from infisical_plugin.sources import InfisicalBulkSource, InfisicalSource

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")


def documented_keys() -> set:
    """YAML keys appearing in the README's fenced config example."""
    blocks = re.findall(r"```yaml\n(.*?)```", README, flags=re.S)
    keys = set()
    for block in blocks:
        keys.update(re.findall(r"^\s*([a-z_]+):", block, flags=re.M))
    return keys


def test_every_schema_key_is_documented():
    documented = documented_keys()
    for source in (InfisicalSource(), InfisicalBulkSource()):
        for key in source.config_schema():
            assert key in documented, (
                f"config_schema key {key!r} of {source.name} missing from README"
            )


def test_auth_sub_keys_are_documented():
    documented = documented_keys()
    for key in ("method", "agent_token_path", "client_id_path", "client_secret_path"):
        assert key in documented, f"auth key {key!r} missing from README"


def test_protected_env_vars_are_documented():
    for var in ("INFISICAL_TOKEN", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET"):
        assert var in README
