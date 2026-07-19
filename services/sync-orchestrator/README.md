# sync-orchestrator

Runs **bare metal** (or a privileged container with `--device` passthrough),
not through `docker-compose.yml`, because it needs the connected iPod
visible as a mounted USB block device — see
`music-stack-planning.md` §2 and §6.

Not implemented yet. Landing in M6/M7, after the iOpenPod headless-usability
spike described in the planning doc.
