from __future__ import annotations

import json
from pathlib import Path

CHATGPT_CONNECTOR_URL = (
    "https://chatgpt.com/plugins#settings/Connectors?create-connector=true"
    "&redirectAfter=%2Fplugins"
)


def test_setup_strings_include_exact_openai_setup_links() -> None:
    string_files = [
        Path("custom_components/hass_codex_tunnel_mcp/strings.json"),
        Path("custom_components/hass_codex_tunnel_mcp/translations/en.json"),
    ]

    for string_file in string_files:
        strings = json.loads(string_file.read_text(encoding="utf-8"))
        description = strings["config"]["step"]["user"]["description"]

        assert "https://platform.openai.com/settings/organization/tunnels" in description
        assert "https://platform.openai.com/settings/organization/api-keys" in description
        assert CHATGPT_CONNECTOR_URL in description
        assert "tunnel_..." in description
        assert "not the runtime API key" in description
