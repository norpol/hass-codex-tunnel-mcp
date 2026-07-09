# OpenAI Tunnel for HA-MCP

A HACS custom integration that runs a local HA-MCP server inside Home Assistant
and exposes it through `openai/tunnel-client` `v0.0.10`.

The integration downloads the host-specific `tunnel-client` release binary on
first run, verifies the pinned SHA256 digest, extracts it under
`.hass_codex_tunnel_mcp/bin/v0.0.10/`, and supervises it as a Home Assistant
subprocess.

## Install with HACS

1. In HACS, add this repository as a custom repository with category
   `Integration`.
2. Install **OpenAI Tunnel for HA-MCP**.
3. Restart Home Assistant.
4. Add the integration from **Settings > Devices & services**.

## OpenAI Setup

1. Create or inspect a tunnel in OpenAI Platform Tunnels.
2. Create a runtime API key with **Tunnels Read** and **Tunnels Use**.
3. Configure this integration with:
   - `CONTROL_PLANE_TUNNEL_ID`, formatted as `tunnel_` followed by 32 lowercase
     hex characters.
   - The runtime API key. This is stored in the Home Assistant config entry and
     passed to `tunnel-client` as `env:CONTROL_PLANE_API_KEY`.
   - Optional control-plane base URL/path for non-default environments.
4. After the tunnel status entity reports ready, configure the ChatGPT/OpenAI
   connector to use the tunnel.

## Runtime Behavior

The integration starts HA-MCP on `127.0.0.1` with an internal secret path, then
starts:

```bash
tunnel-client run \
  --control-plane.tunnel-id <id> \
  --control-plane.api-key env:CONTROL_PLANE_API_KEY \
  --mcp-server-url channel=main,url=http://127.0.0.1:<ha-mcp-port><secret-path> \
  --health.listen-addr 127.0.0.1:0 \
  --health.url-file <integration-run-dir>/health.url
```

Only Linux `amd64` and `aarch64` Home Assistant OS/Supervised hosts are
supported initially. Other platforms create a Home Assistant repair issue.

## Entities

- Tunnel process state
- Tunnel ready state
- Installed `tunnel-client` version

## Services

- `hass_codex_tunnel_mcp.restart_tunnel`
- `hass_codex_tunnel_mcp.redownload_tunnel_client`
- `hass_codex_tunnel_mcp.restart_mcp_server`

## Development Checks

```bash
python -m compileall custom_components tests
pytest
```
