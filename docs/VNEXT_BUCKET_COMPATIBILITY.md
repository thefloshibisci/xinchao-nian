# vNext Memory Bucket Compatibility

This document describes the isolated `xinchao-nian` vNext contract. It does
not authorize changing the production Xinchao endpoint, MCP configuration, or
Ombre Brain data volume.

| Bucket type | Pulse map | Exact detail | Legacy MCP fallback |
| --- | --- | --- | --- |
| `dynamic` | `💭` or `✅` | `GET /api/bucket/{id}` | exact-ID breath |
| `permanent` | `📦` or `📌` | `GET /api/bucket/{id}` | exact-ID breath |
| `feel` | `🫧` | `GET /api/bucket/{id}` | `feel` or legacy feel-domain breath |
| `plan` | `📋` | `GET /api/bucket/{id}` | no reliable exact reader |
| `letter` | `💌` | `GET /api/bucket/{id}` | not exposed through Xinchao's public proxy |
| `i` | `🪞` | `GET /api/bucket/{id}` | no bounded exact reader |
| `archived` | `🗄️` only when explicitly listed | same endpoint, but Xinchao does not list it by default | excluded from breath |

The detail endpoint accepts either a normal Dashboard session or
`OMBRE_MCP_SERVICE_TOKEN`. Service-token access is limited to this read-only
route. Bucket IDs must first appear in Xinchao's cached `pulse` metadata before
Xinchao requests the detail, so the endpoint cannot be used as an arbitrary
file reader through the Dashboard API.

The production Ombre Brain may not provide service-token access to the detail
route. In that case Xinchao keeps its legacy MCP fallback, but `plan`, `letter`,
and `i` detail is not considered fully compatible until the isolated vNext
Ombre Brain is deployed and accepted.
