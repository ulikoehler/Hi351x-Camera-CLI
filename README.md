# CameraControl

CLI tool to scan, dump, apply and edit the configuration of hf*/hi3510-family
IP cameras ("IPCamera", e.g. `deyType=H2S02P100000`) over their HTTP JSON API.

See [INTERFACES.md](INTERFACES.md) for the full protocol documentation
(HTTP `/action/*` API, ONVIF :8080, RTSP :554, legacy CGI, port 8000).

No dependencies — Python 3.7+ standard library only.

## Quick start

```bash
# Find compatible cameras on the network (probes hiLogin)
python3 camera_config.py scan 10.91.1.0/24 -p 123456

# List IPs, MAC addresses and device IDs
python3 camera_config.py mac 10.91.1.0/24

# Dump full config of one device or the whole subnet -> dumps/<ip>.json
python3 camera_config.py dump 10.91.1.209 -o dumps/
python3 camera_config.py dump 10.91.1.0/24 -o dumps/

# Apply a dump to one device or the whole subnet
python3 camera_config.py apply 10.91.1.0/24 -i dumps/10.91.1.200.json --skip-network
```

## Inspecting and changing properties

```bash
# List all get/set config endpoints
python3 camera_config.py list 10.91.1.209

# Show a section's live properties (channelized endpoints auto-iterate)
python3 camera_config.py list 10.91.1.209 getVencConf
python3 camera_config.py list 10.91.1.0/24 getRtspConf

# Change individual properties on one device or a subnet
python3 camera_config.py set 10.91.1.209 getVencConf bitrate=2048 --channel 0
python3 camera_config.py set 10.91.1.0/24 setRtspConf auth=1
python3 camera_config.py set 10.91.1.203 getSysConfig dev_name=CAM-203
```

Values after `key=` are parsed as JSON, so numbers, booleans, quoted strings
and arrays/objects all work. Endpoint names accept either the `get*` or `set*`
form. `--channel` selects the stream for channelized endpoints (0=main,
1=sub).

## Update-only-if-changed

Both `apply` and `set` first read the live configuration and send **only the
fields that actually differ** — devices already matching the target state are
not touched and report `unchanged`.

- `deviceTime` is ignored in comparisons (it drifts); when time settings do
  differ, a *fresh* timestamp is sent (the firmware requires `deviceTime`
  and rejects stale ones).
- `--force` on `apply`/`set` bypasses the diff check and writes everything.
- `channel` is a selector, never treated as a difference.

## Safety

- Devices are detected by a successful `hiLogin`; foreign device types are
  skipped, and `apply` additionally refuses `deyType` mismatches.
- Passwords/secrets (`passwd`, `smtp_passw`, `AP_Passwd`, `gb_passwd`,
  Wi-Fi `passwd`) are stripped from apply payloads.
- `--skip-network` prevents pushing wired/Wi-Fi settings that could strand
  devices on a different subnet.
- Destructive endpoints (`reset`, `restart`, `fwUpgrade`, `diskFormat`,
  `setPasswd`) are never invoked by this tool.

## Useful reference values

`getVencConf` / `setVencConf`:

| key | values |
|---|---|
| `encode_type` | 1=H.264, 3=MJPEG, 5=H.265, 6=H.265+ |
| `encode_profile` | 0/1/2 (complexity; 2 only for H.264) |
| `rc_mode` | 0=CBR, 1=VBR |

## Files

- `camera_config.py` — the tool
- `INTERFACES.md` — protocol investigation notes
- `dumps/` — config snapshots (gitignored; may contain secrets)
