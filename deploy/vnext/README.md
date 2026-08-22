# vNext isolated deployment

This directory describes the two-service Zeabur topology for the isolated
Ombre Brain vNext experiment. It is intentionally separate from the current
production Xinchao service and the original Ombre Brain deployment.

## Services

Create two Zeabur services from the same repository and branch:

| Service | Dockerfile | Container port | Persistent volume | Public MCP |
| --- | --- | ---: | --- | --- |
| `ombre-brain-vnext-ob` | `/ombre-brain/Dockerfile` | `8000` | `ombre-vnext-buckets:/app/buckets` | No |
| `ombre-brain-vnext-xinchao` | `/Dockerfile` | `18110` | `xinchao-vnext-state:/app/state` | Yes |

The existing `ombre-brain-vnext` service is the OB half of this topology. Do
not change its Dockerfile, volume, or environment until the read-only facts in
`docs/VNEXT_DEPLOYMENT_AUDIT.md` have been checked against the console.

## Internal addresses

Use the private service address shown by Zeabur for each service. Do not assume
that a public URL or a service display name is resolvable inside the project.

Set these values after both services exist:

```text
Xinchao OMBRE_MCP_URL   = http://<ob-private-host>:8000/mcp
OB DYNAMIC_MIND_URL     = http://<xinchao-private-host>:18110
```

Only the Xinchao service should receive the public HTTPS domain used by phone
and desktop MCP clients. The OB service should remain private, or have a
separately protected management URL that is not used as an MCP connector.

## Independent secrets

Generate three different random values, each at least 32 characters:

```text
OB_MCP_TOKEN       -> OB OMBRE_MCP_SERVICE_TOKEN and Xinchao OMBRE_MCP_TOKEN
XINCHAO_TOKEN     -> Xinchao SERVICE_TOKEN and OB DYNAMIC_MIND_TOKEN
OAUTH_APPROVAL    -> Xinchao OAUTH_APPROVAL_TOKEN only
```

The Dashboard password and Dashboard access token must be different from all
of the above. Never copy a production token into either vNext service.

The example files in this directory are templates only. Put their values into
Zeabur environment variables or a private local file; do not commit a filled
copy.

## Safe order

1. Confirm the current OB service process, port, volume, and branch without restarting it.
2. Create the separate Xinchao service from the root `Dockerfile` with its own state volume. Do not select or edit `xinchao-nian-caric`.
3. Expose the OB container port `8000` to the project network only; do not make it the public MCP connector.
4. Configure private service-to-service addresses and independent secrets. Run `node tools/vnext_config_check.mjs ob` and `node tools/vnext_config_check.mjs xinchao` from private environment sessions before saving variables.
5. Run `node tools/vnext_preflight.mjs` against the public Xinchao URL and, if available, the private OB URL.
6. Keep OB writes disabled until the test-data copy is ready. Then enable writes only for the isolated copy and run the acceptance checklist.
7. Do not discuss a production migration until both phone and desktop MCP acceptance reports are complete.

## Read-only preflight

Unauthenticated checks only inspect health, version, and the expected public
boundary:

```powershell
node tools/vnext_preflight.mjs --xinchao https://<vnext-xinchao-domain>
```

To additionally run MCP `initialize` and `tools/list`, provide tokens through
the process environment. The script never prints their values and never calls
an MCP tool that can write memory:

```powershell
$env:VNEXT_XINCHAO_TOKEN = '<private-value>'
$env:VNEXT_OB_TOKEN = '<private-value>'
node tools/vnext_preflight.mjs `
  --xinchao https://<vnext-xinchao-domain> `
  --ob http://<ob-private-host>:8000
```

The preflight is diagnostic only. It does not deploy, restart, import, mutate
volumes, or call `hold`, `grow`, `trace`, `feel`, `dream`, or any other tool.
