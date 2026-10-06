# Runtime selections and credential handoff

Updated: 2026-10-06. These are recorded design inputs, not an enabled configuration. No credentials are stored here.

## Confirmed by the owner

- Model family/version: **DeepSeek V4.1 Flash**.
- Discord channel ID: **`1557157266824634469`**. Store this identifier as a string, not a floating-point number.
- Notify the owner when a Treg token or other credentials become necessary. Do not ask for secrets just to review architecture.
- The owner wants to review the architecture before implementation or activation.

## Mapping into the existing design

Use DeepSeek V4.1 Flash for both extraction arms. Applying the same selected model to the initial fixed judge is a reversible simplicity default, not a separate owner-approved judge-model decision. The judge uses its own isolated invocation and fixed rubric, never the extractor's conversation, strategy labels, confidence, or reasoning. A same-model judge is not independent ground truth.

The official DeepSeek API documents `deepseek-flash` as the current API identifier for V4.1 Flash. This identifies the model on DeepSeek's official endpoint; a different provider may use a different identifier. The provider/account, endpoint, credentials, and hard budget still need to be checked before live use. Do not change the user's global Hermes model setting. Pin the project's per-run settings instead.

A hosted alias is not a guarantee of permanently fixed weights. Record the requested model, provider, returned model/version or fingerprint when available, and request date. Do not silently switch models or fall back during a comparison. If a backend revision is detected, start a new experiment epoch rather than pool incompatible results.

The channel ID is an agreed destination, not proof of bot membership, posting/file permissions, a configured token, or authorization to send now. Reuse Hermes's existing Discord connection if it is present and authorized; verify access before asking for any missing secret. Send time and explicit activation remain unset.

## When credentials are needed

| Stage | Credentials needed | Current action |
|---|---|---|
| Architecture, public Treg catalog/docs, local fixtures | No Treg token | Review and document only |
| First authorized Treg `/call/` smoke test | Treg account/team API token; approved balance/budget for a metered endpoint | Ask at this boundary; use an approved secret store/environment, not chat or project docs |
| Selected endpoint requires BYOK | That provider's key, only if the chosen route actually requires it | Report the exact provider, endpoint, purpose and billing path before requesting it |
| First authorized DeepSeek invocation | Credentials for the chosen DeepSeek-serving provider | Check an existing configured route without printing secrets; request a missing credential only then |
| First authorized Discord delivery | Authorized Hermes bot connection and channel access | Channel already supplied; verify existing configuration rather than ask again |

For raw REST calls, Treg documents `X-Treg-Token`, not an `Authorization: Bearer` header containing the Treg token. Bearer authentication is documented for MCP. Public catalog routes need no token. Eligible catalog endpoints can use Treg-managed provider credentials and a prepaid balance; other endpoints can require BYOK. Therefore, **do not assume the user needs Reddit/X developer keys or browser cookies, and do not promise no provider key will ever be needed**. Confirm the selected endpoint's credential mode first.

Treg documentation and public catalog inspection do not prove successful live Reddit/X access from this VPS. Provider selection, retention rights, billing units, pagination, comments coverage and reliability remain preflight checks. No paid calls, registration, credit top-ups, jobs, messages or runtime configuration changes were performed for this update.

## Primary references checked on 2026-10-06

- DeepSeek API changelog: https://api-docs.deepseek.com/updates/ — current `deepseek-flash` mapping to V4.1 Flash.
- Treg API reference: https://treg.to/docs — public catalog access, REST token header, caller-selected provider, metered/BYOK paths.
- Treg protocol: https://treg.to/llms.txt — dashboard/team API tokens and endpoint credential modes.
- Discord API reference: https://docs.discord.com/developers/reference — authentication is separate from a channel identifier.

See [architecture.md](architecture.md) for the visual system map and [activation-checklist.md](activation-checklist.md) for the remaining activation gates.
