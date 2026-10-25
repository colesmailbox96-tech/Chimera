# Chimera — Icarus Provider Scheduler Shell

This public repository contains only the lightweight provider-control service for Icarus.

It contains **no model weights, private training data, secrets, or account credentials**.

The scheduler:
- tracks provider health and suitability,
- prefers legitimate zero-cost capacity,
- checkpoints before provider handoff,
- refuses unavailable targets,
- does not create extra accounts, reset quotas, disguise usage, or exceed provider limits.

Current provider roles:
- Vercel/browser: active inference + evidence API
- Render: standby control plane
- Railway: candidate, currently unavailable through the connector

Training state remains external to this public scheduler shell.
