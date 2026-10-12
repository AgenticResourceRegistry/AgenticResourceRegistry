<!-- SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Sync an MCP server from GitHub or GitLab

An MCP server listed with a git repository can publish new versions on its own. Add a webhook to the repository on GitHub or GitLab, and Observal re-reads the repository and publishes a new version whenever you push or publish a release. You no longer have to run `observal registry mcp edit` after every change.

GitHub (github.com and GitHub Enterprise) and GitLab (gitlab.com and self-managed GitLab) are supported.

## What a sync does

1. GitHub or GitLab sends a webhook to Observal.
2. Observal authenticates it and checks the repository. Then it queues the sync and replies right away.
3. A background worker fetches the repository, runs the same analysis as `observal registry mcp submit --git`, and publishes a new version.

Each sync creates a new version and leaves earlier versions alone. Agents pinned to an earlier version keep installing it, because agent lockfiles pin version content by digest.

The new version starts as a copy of the current version, then picks up these changes from the repository:

| Field | What changes |
| --- | --- |
| Tools | Replaced with the tools found in the repository |
| Environment variables | Newly found variables are added as optional. Variables you already listed keep your descriptions |
| Framework | Updated when one is detected |
| Command, args, Docker image, setup instructions | Filled in only when the listing has none, and only from an image the repository declares in a compose file or README. A guessed image name is never stored. Values you set by hand are kept |
| Description | Kept |
| Commit and ref | The synced commit sha and the branch or tag name are recorded on the version |
| Changelog | The head commit message for a push, or the release notes for a release |

Synced versions are approved and published right away, without review. Anyone who can push to the tracked branch, or publish a release, can change what the listing installs. Turn on sync only for repositories whose write access you trust.

## Triggers and version numbers

Choose either trigger, or both:

| Trigger | Fires on | Version |
| --- | --- | --- |
| Push | A push to the tracked branch. The default is the repository's default branch | The version in `pyproject.toml` or `package.json` when it is higher than every existing version. Otherwise the highest existing version with its patch number raised, so `1.2.3` becomes `1.2.4` |
| Release | A GitHub release being published (drafts are ignored), or a GitLab release being created | The release tag, either `v1.2.3` or `1.2.3`. A tag that is not a semantic version is ignored |

Pushing a tag on its own does not sync. Create a release from the tag.

If fetching the repository fails, the sync retries twice, 15 and then 30 seconds later, before it is marked failed. This covers network errors and a release whose tag is still being published.

A push sync always fetches the branch tip when the job runs, not the commit named in the delivery. Several quick pushes therefore publish the newest code, and a commit that is already published is skipped. If an earlier sync finishes after a later one, it is skipped too, so older code never becomes the latest version. A release whose version already exists is skipped too. A release older than the current latest version is published but does not become the latest.

## Set it up

You must own the listing, or be a co-author or an admin. The listing must have a git repository URL, and a reviewer must have approved it at least once. Synced versions skip review, so the first version still goes through it.

### 1. Turn on sync

From the CLI:

```bash
observal registry mcp sync enable alice/weather-mcp                    # push only
observal registry mcp sync enable alice/weather-mcp --release          # push and release
observal registry mcp sync enable alice/weather-mcp --release --no-push --branch stable
observal registry mcp sync enable alice/weather-mcp --provider gitlab  # self-hosted GitLab
```

Or open the MCP server in the web app and use the **Sync** tab.

Observal picks the provider from the repository URL: a hostname containing `gitlab` means GitLab, anything else means GitHub. For a self-managed GitLab on another hostname, such as `code.acme.com`, choose GitLab with `--provider gitlab` or the **Repository host** setting in the Sync tab.

Observal returns a webhook URL and a secret. The secret is shown only once. If you lose it, rotate it with `observal registry mcp sync rotate-secret`.

### 2. Add the webhook

#### GitHub

In the repository, open **Settings > Webhooks > Add webhook** and enter:

| Setting | Value |
| --- | --- |
| Payload URL | The webhook URL from step 1 |
| Content type | `application/json` |
| Secret | The secret from step 1 |
| Events | **Just the push event** for push sync. For release sync, choose **Let me select individual events** and tick **Releases**, plus **Pushes** if you use both |

GitHub sends a `ping` right away. Its delivery should show a `202` response.

#### GitLab

In the project, open **Settings > Webhooks > Add new webhook** and enter:

| Setting | Value |
| --- | --- |
| URL | The webhook URL from step 1 |
| Secret token | The secret from step 1 |
| Trigger | **Push events** for push sync, **Releases events** for release sync, or both. You can limit push events to the tracked branch, though Observal ignores other branches anyway |
| SSL verification | Keep it on |

GitLab has no ping event. Use **Test > Push events** on the saved webhook to send a sample push. It syncs the tracked branch, or is skipped if that commit is already published.

The provider must be able to reach your Observal server. Set `deployment.public_url` so the webhook URL uses your public address. If Observal runs on a private network, see [Private deployments](#private-deployments).

### 3. Check the result

```bash
observal registry mcp sync status alice/weather-mcp
observal registry mcp sync run alice/weather-mcp   # sync the tracked branch now
```

Deliveries that are ignored, such as a push to another branch, do not change the sync status. The provider's delivery log (**Recent Deliveries** on GitHub, **Recent events** on GitLab) shows the reason in the response body.

## Private deployments

github.com and gitlab.com deliver webhooks over the public internet, so an Observal install inside a private network needs one public path for them. Expose only the webhook receivers, `POST /api/v1/webhooks/github/mcp/<id>` and `POST /api/v1/webhooks/gitlab/mcp/<id>`, through a public gateway, load balancer or reverse proxy that answers `404` to every other path. Each delivery is authenticated by its signature or secret token, so a source IP allowlist is optional. Then set `WEBHOOK_PUBLIC_URL` on the API to that public address, for example `https://hooks.observal.example.com`. The **Sync** tab shows webhook URLs under it, while everything else keeps using the private address.

* **AWS Terraform:** set `enable_webhook_ingress = true` and list your providers in `webhook_providers` to add an API Gateway endpoint that does this. See [Git webhooks on a private install](../self-hosting/aws-terraform.md#git-webhooks-on-a-private-install).
* **GitHub Enterprise Server or self-managed GitLab** on the same network can reach Observal directly and needs none of this. Self-managed GitLab blocks webhooks to private addresses by default; an administrator allows them under **Admin > Settings > Network > Outbound requests**. If GitLab reports `URL is blocked: Host cannot be resolved or invalid`, the GitLab server cannot resolve Observal's hostname.

## Private repositories

Observal fetches the repository with the server's `GIT_CLONE_TOKEN`, the same token used to analyze git submissions. The token needs read access to the repository. For GitLab, set `GIT_CLONE_TOKEN_USER=oauth2`. A git server on a private network also needs `ALLOW_INTERNAL_GIT_URLS=true`.

## Security

- Each listing has its own secret, stored encrypted. GitHub deliveries need a valid `X-Hub-Signature-256` signature and GitLab deliveries the matching `X-Gitlab-Token`; anything else is rejected with `401`.
- Each listing answers only on its own provider's endpoint, so a GitLab secret is never accepted as a GitHub signature or the reverse.
- A delivery from a different repository than the listing's git URL is rejected with `422`.
- Synced versions are published as the user who last turned on or changed sync. If that user no longer has owner-level access to the listing (as its owner, a co-author or an admin), or was removed from the teamspace of a private listing, syncs fail until an owner turns sync off and on again.
- The receivers allow 60 deliveries per minute per client address.

## Why sync is one way

Sync only reads from the repository; editing the listing in Observal never writes back to GitHub or GitLab. The repository is the source of truth for the server's code, and the fields Observal derives from it (tools, environment variables, version) are a read of that code. Writing back would need a token that can push to every synced repository, could loop (a push syncs, the sync pushes), and would race with the repository's own history. What you edit in Observal, such as the description or environment variable descriptions, is listing metadata and is kept across syncs.

## Turn it off

```bash
observal registry mcp sync disable alice/weather-mcp
```

Published versions stay. Remove the webhook from the repository's settings as well.
