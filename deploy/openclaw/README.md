# OpenClaw stdio MCP overlay

When OpenClaw itself runs in Docker, it cannot safely spawn
`docker compose run ...` without a Docker socket. Build this overlay instead:
it lets OpenClaw spawn `jev-mobile-mcp` as an ordinary local stdio child.

The overlay contains only the Jev MCP Python runtime. Mount the worker's local
SQLite data directory at `/data` and configure the child with
`JEV_MOBILE_DB=/data/jev-mobile.db`. Do not mount `/dev/bus/usb`, ADB keys, a
Docker socket or Portal credentials into OpenClaw.

Register it through OpenClaw's supported command, for example:

```text
openclaw mcp add jev-mobile --command jev-mobile-mcp \
  --env JEV_MOBILE_DB=/data/jev-mobile.db \
  --include start_task,get_task,get_task_events,cancel_task,answer_task,get_device_status
```

The durable `jev-mobile` worker remains the only component with Android access.
