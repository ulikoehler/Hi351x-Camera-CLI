# Hi351x-Camera-CLI

Command-line tool to discover, inspect, dump, clone and edit the
configuration of **hi3510-family IP cameras** (HiSilicon **Hi3516CV610** and
related SoCs, ONVIF stack `hfonvif`, firmware `22.010.x_MAIN_V4x`) over their
built-in HTTP JSON API — no SDK, no vendor software, Python standard library
only.

Built for fleets: every command accepts a single IP **or an entire CIDR
subnet**, probes devices in parallel, skips incompatible hardware, and only
writes settings that actually differ.

> Typical devices: unbranded/OEM "IPCamera" units (XMEye/CamHi-style boards)
> exposing the `/action/*` JSON API. See [INTERFACES.md](INTERFACES.md) for the
> complete protocol documentation.

## Features

- **Network discovery** — scans subnets in parallel, confirms devices via
  `hiLogin`, reports MAC + device ID
- **Full config dump** — 40+ config sections to a JSON file per device
- **Config apply/clone** — replicate one camera's config across a fleet
- **Property-level access** — read/write individual settings on one device or
  many
- **Idempotent writes** — reads live state first, sends only fields that
  differ; devices already matching report `unchanged` and are not touched
- **Safety guards** — secrets stripped on apply, `--skip-network` prevents
  self-inflicted IP changes, `deyType` mismatch blocks cross-model applies,
  destructive endpoints (reset/upgrade/format) are never called

## Requirements

- Python ≥ 3.7 — **zero dependencies** (stdlib only)
- Network reachability to the cameras on TCP port 80
- Admin credentials (default on these devices: `admin` / `123456`)

## Install

```bash
git clone https://github.com/ulikoehler/Hi351x-Camera-CLI.git
cd Hi351x-Camera-CLI
python3 camera_config.py --help
```

## Authentication

All commands take `--password` / `-p` (default `123456`). The tool sends the
**MD5 of the password** to `/action/hiLogin`, exactly as the device's own web
UI does. The username is fixed to `admin` on this firmware — the API does not
send a username at all.

## Command reference

### `scan` — find compatible devices

```bash
python3 camera_config.py scan 192.168.1.0/24 -p 123456
```

Probes every host in the target (single IP, hostname or CIDR), performs
`hiLogin`, and prints each responding device's ID and board type
(`deyType`). Non-camera hosts and non-matching firmware are silently skipped.

### `mac` — list MAC addresses and device IDs

```bash
python3 camera_config.py mac 192.168.1.0/24
```

```
192.168.1.168      bc-07-18-03-31-ea  H0100011A120100011786
192.168.1.169      bc-07-18-03-31-ed  H0100011A120100011789
...
```

### `list` — inspect endpoints and properties

```bash
# All known get/set endpoint names
python3 camera_config.py list 192.168.1.168

# Live properties of one section, on one device or a whole subnet
python3 camera_config.py list 192.168.1.168 getVencConf
python3 camera_config.py list 192.168.1.0/24 getRtspConf
```

Channelized endpoints (e.g. `getVencConf`) automatically iterate streams —
`ch0` = main stream, `ch1` = sub stream — and stop at the first empty channel.

### `set` — change individual properties

```bash
python3 camera_config.py set <target> <endpoint> key=value [key=value...]
```

```bash
# Sub-stream bitrate on one camera
python3 camera_config.py set 192.168.1.168 getVencConf bitrate=512 --channel 1

# Enable RTSP auth on every camera in the subnet
python3 camera_config.py set 192.168.1.0/24 setRtspConf auth=1

# Rename a device (shows in its web UI title)
python3 camera_config.py set 192.168.1.168 getSysConfig dev_name=CAM-203

# OSD title (arrays/objects via JSON values)
python3 camera_config.py set 192.168.1.168 getOsdConf \
    'title_list=[{"title":"WAREHOUSE","title_pos_x":556,"title_pos_y":546,"show_title":1}]'
```

- Endpoint names accept either the `get*` or `set*` form (mapped automatically).
- Values are parsed as JSON: `554`→int, `true`→bool, `"text"`/bare→string,
  `[...]`/`{...}`→arrays/objects.
- `--channel N` is required for per-stream endpoints.
- Reads the live value first; sends nothing when already matching
  (`unchanged`). `--force` sends unconditionally.

### `name` — set the device name

```bash
python3 camera_config.py name 192.168.1.168 CAM-01
python3 camera_config.py name 192.168.1.0/24 IPCamera   # whole subnet
```

Sets `dev_name` (shown in the web UI title and device lists). Idempotent:
already-matching devices report `unchanged`.

### `dhcp` / `static` — switch IP address mode

```bash
# Switch to DHCP (device re-leases; may move to a different IP — scan after)
python3 camera_config.py dhcp 192.168.1.168

# Switch to a fixed static IP
python3 camera_config.py static 192.168.1.168 \
    --ip 192.168.1.168 --gateway 192.168.1.1 --dns 192.168.1.1 --dns2 8.8.8.8
```

Network changes restart the camera's network stack — the connection drops
mid-request. The tool tolerates this, waits, re-logs in and verifies the new
state (retrying once if needed). If the device leased a different address and
doesn't come back at the expected IP within ~30 s, run `scan`/`mac` on the
subnet to find it. Keeping `--ip` equal to the current address is the safe
option for `static`.

### `dump` — snapshot full configuration

```bash
python3 camera_config.py dump 192.168.1.168 -o dumps/
python3 camera_config.py dump 192.168.1.0/24 -o dumps/
```

Writes `dumps/<ip>.json` containing `_meta` (login info, device type) and
`sections` (every get-endpoint's full response; channelized endpoints stored
as a list per channel). Sections that fail are recorded as `{"_error": ...}`
and skipped on apply.

### `apply` — replicate a dump to devices

```bash
# Clone .200's config onto every camera in the subnet
python3 camera_config.py apply 192.168.1.0/24 -i dumps/192.168.1.168.json --skip-network
```

Per section prints: `unchanged` / `updated (n fields)` / `skipped` / error.

- Only differing fields are POSTed (read–diff–write per section).
- `--skip-network` excludes `getWiredNetwork`/`getWifiConfig` — strongly
  recommended when cloning across devices (a source's static IP would move
  every target to the same address).
- `--force` disables diffing and writes full sections.
- Secrets are never pushed (see Safety).
- Apply is refused when the target's `deyType` differs from the dump's.

## Dump file format

```json
{
 "_meta": {"ip": "192.168.1.168", "hiLogin": {"deviceID": "...", "deyType": "H2S02P100000", ...}},
 "sections": {
  "getVencConf": [ {"channel": 0, "pic_width": 1920, ...}, {"channel": 1, ...} ],
  "getRtspConf": {"enable": 1, "rtsp_port": 554, ...},
  ...
 }
}
```

Dumps are plain JSON — edit them freely before applying (e.g. delete sections
you don't want pushed). **Do not commit dumps to git**: they can contain
credentials (`.gitignore` already excludes `dumps/`).

## Property reference

### Encoder (`getVencConf`, per `--channel`)

| key | values |
|---|---|
| `encode_type` | `1`=H.264, `3`=MJPEG, `5`=H.265, `6`=H.265+ |
| `encode_profile` | `0`/`1`/`2` complexity (`2` only valid for H.264) |
| `rc_mode` | `0`=CBR, `1`=VBR |
| `pic_width`/`pic_height` | must match an entry of `pixel_list` |
| `frame_rate`, `gop`, `bitrate` | fps / GOP length / kbps |

### Frequently used endpoints

`getSysConfig` (dev_name, language, webPort) · `getDeviceTime` (NTP, timezone)
· `getRtspConf` · `getAencConf` (audio) · `getOsdConf` (overlays)
· `getImageAdjustment` (+Ex) · `getViMask` (privacy mask) · `getMotionDetConf`
· `getMailConf` · `getGb28181` · `getWifiConfig` · `getRecSchedule`
· `getStorageRule` · `getPeripheralConf` (RS485/PTZ) · `getRebootConf`

Full inventory with descriptions: [INTERFACES.md](INTERFACES.md).

## Behaviour notes

- **Responses**: `code: 0` = success; some `set*` endpoints reply `code: 1`
  yet still apply the change — the tool re-reads after writes where it
  matters, and `list`/`set` verify state, not just return codes.
- **`deviceTime`** drifts every second. It's ignored in diffs; when other
  time settings differ, `apply` sends a *fresh* timestamp (the firmware
  rejects stale ones with `code=201`).
- **Unsupported sections**: endpoints returning `code -1/209/...` on a given
  model (e.g. `getIVSConf`, `getSmartAudioConf` on this hardware) are dumped
  for completeness and skipped on apply.
- **Unknown hosts**: anything not answering `hiLogin` with `code=0` is
  ignored — safe to point at mixed subnets.

## Safety summary

- Stripped from apply payloads: `passwd`, `password`, `AP_Passwd`,
  `smtp_passw`, `gb_passwd`, plus all response metadata (`code`, `deviceID`,
  `sign_tby`, …).
- Never called: `reset`, `restart`, `fwUpgrade`, `diskFormat`, `setPasswd`,
  `addUser`/`deleteUser` (user management is intentionally out of scope —
  use `setPasswd`-aware tooling if needed).
- Read endpoints used for diffing are exactly the same `get*` calls the
  camera's own web UI makes.

## Testing performed

Verified against a live fleet of ten identical cameras
(192.168.1.168–192.168.1.177, Hi3516CV610, firmware `22.010.30.6_MAIN_V44`):

- **Discovery**: parallel `hiLogin` scan correctly identifies all 10 devices;
  non-camera IPs ignored. `mac` returns the real `bc:07:18:*` addresses.
- **Dump**: all 44 sections captured; `getVencConf` correctly enumerated 2
  streams (main 2592×1944-capable, sub 704×576); unsupported endpoints
  recorded without aborting.
- **Apply round-trip**: a device's own dump re-applied → every section
  reported `unchanged`, zero writes issued.
- **Cross-device clone**: main-stream profile (1920×1080, 25 fps, GOP 50,
  4096 kbps, MJPEG) pushed to 9 cameras; re-reads confirmed every field.
- **`set` diff logic**: same-value `set` produced `unchanged` with no POST;
  differing `set` transmitted only the changed keys.
- **`set` live change**: sub-stream bitrate 1024→512→1024 on one device,
  verified by re-reading.
- **Time handling**: after `ntp_enable` was changed outside the tool, `apply`
  correctly detected the single-field diff and `setTime` succeeded with a
  fresh timestamp (stale dump timestamps correctly reproduce the firmware's
  `code=201` rejection without the freshness fix).
- **Stale-dump semantics**: applying an older dump correctly reverted the
  codec to the dump's value — `apply` means "make it look like the dump",
  diffs are computed from live state, not from assumptions.

Not covered by testing: Wi-Fi/4G features (hardware present but untested),
PTZ movement, firmware upgrade paths, SD playback/record scheduling beyond
config read/write, and non-`H2S02P100000` hardware.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No compatible devices found` | wrong subnet/password, or non-hi3510-family cameras |
| `login failed` / `code != 0` on hiLogin | password is not the md5-able admin password |
| `set ... code=201 time error` | stale timestamp — update via `apply` (sends fresh time) or set `deviceTime=$(date +%s)` |
| `set ... code=-1` / `209` | feature not supported by this hardware/firmware |
| Device unreachable after apply | `getWiredNetwork` pushed without `--skip-network` |

## License

Apache License 2.0 — see [LICENSE](LICENSE).
