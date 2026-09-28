#!/usr/bin/env python3
"""
camera_config.py — Dump & apply configuration for hf*/hi3510-family IP cameras.

Interface: HTTP POST JSON API at http://<ip>/action/<endpoint>
Auth:      POST /action/hiLogin {"passwd": md5(password)}

Usage:
  Scan a network for compatible devices:
      python3 camera_config.py scan 10.91.1.0/24 --password 123456

  Dump config from one device or a whole subnet:
      python3 camera_config.py dump 10.91.1.209 --password 123456 -o dumps/
      python3 camera_config.py dump 10.91.1.0/24 --password 123456 -o dumps/

  Apply a dumped config to one device or a whole subnet:
      python3 camera_config.py apply 10.91.1.0/24 --password 123456 -i dumps/10.91.1.209.json

Only devices answering hiLogin with code==0 are touched; mismatched device
types (dev_type/deyType differing from the dump) are skipped.
"""

import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import os
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Endpoint map: get-name -> set-name (None = read-only / action only)
# ---------------------------------------------------------------------------
ENDPOINTS = {
    "getSysConfig":         "setSysConfig",
    "getWiredNetwork":      "setWiredNetwork",
    "getDeviceTime":        "setTime",
    "getRtspConf":          "setRtspConf",
    "getVencConf":          "setVencConf",       # channelized
    "getAencConf":          "setAencConf",
    "getOsdConf":           "setOsdConf",
    "getImageAdjustment":   "setImageAdjustment",
    "getImageAdjustmentEx": "setImageAdjustmentEx",
    "getViMask":            "setViMask",
    "getMotionDetConf":     "setMotionDetConf",
    "getNetAlarmConf":      "setNetAlarmConf",
    "getViLoseAlarmConf":   "setViLoseAlarmConf",
    "getODAlarmConf":       "setODAlarmConf",
    "getAlarmInConf":       "setAlarmInConf",
    "getMailConf":          "setMailConf",
    "getRtmpConf":          "setRtmpConf",
    "getRtpConf":           "setRtpConf",
    "getTcpTransfer":       "setTcpTransfer",
    "getGb28181":           "setGb28181",
    "getWifiConfig":        "setWifiConfig",
    "getRecSchedule":       "setRecSchedule",
    "getStorageRule":       "setStorageRule",
    "getScheSnap":          "setScheSnap",
    "getPeripheralConf":    "setPeripheralConf",
    "getPTZBrush":          "setPTZBrush",
    "getPtzTrack":          "setPtzTrack",
    "getIVSConf":           "setIVSConf",
    "getSmartAudioConf":    "setSmartAudioConf",
    "GetFaceConfig":        "SetFaceConfig",
    "getIllegalParkingConf":"setIllegalParkingConf",
    "getLeaveThePostConf":  "setLeaveThePostConf",
    "getRegInvConf":        "setRegInvConf",
    "getWiegandConfig":     "setWiegandConfig",
    "getPlatformServer":    "setPlatformServer",
    "getOutputVolume":      "setOutputVolume",
    "getRebootConf":        "setRebootConf",
    "getUUIDConf":          None,
    "getStorageInfo":       None,
    "getAlarmStaus":        None,
    "getGb28181Status":     None,
    "getRtmpStatus":        None,
    "getWifiStatus":        None,
    "getPlaybackTime":      None,
}

CHANNELIZED = {"getVencConf"}
MAX_CHANNEL = 4

# response-only fields stripped before re-applying
META_KEYS = {
    "code", "message", "deviceID", "device_id", "device_mac", "device_ip",
    "log", "sign_tby", "userRole", "failedAttempts", "TempType", "deyType",
    "pixel_list", "max_framerate", "validSupport", "devs", "version",
    "ai_version", "ui_version", "pf_version", "upf_version", "kernel_version",
    "dev_type", "dev_mask", "lang_mask", "ivs_mask", "update_type",
    "defaultFileTime", "defaultFileVar", "defaultFileReadme",
    "driver_board_version", "phoneUUID",
}

# fields that must never be pushed to other devices
SECRET_KEYS = {"passwd", "password", "AP_Passwd", "smtp_passw", "gb_passwd"}

TIMEOUT = 8


class Camera:
    def __init__(self, ip, password, timeout=TIMEOUT):
        self.ip = str(ip)
        self.base = f"http://{self.ip}"
        self.passwd_md5 = hashlib.md5(password.encode()).hexdigest()
        self.timeout = timeout
        self.info = {}

    def post(self, endpoint, payload):
        url = f"{self.base}/action/{endpoint}"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def login(self):
        r = self.post("hiLogin", {"passwd": self.passwd_md5})
        if r.get("code") != 0:
            raise RuntimeError(f"login failed: {r}")
        self.info = r
        return r

    def get(self, endpoint, channel=None):
        payload = {} if channel is None else {"channel": channel}
        return self.post(endpoint, payload)

    def set(self, endpoint, payload):
        return self.post(endpoint, payload)


def clean_for_apply(section):
    """Strip metadata and secrets from a dumped config section."""
    if not isinstance(section, dict):
        return section
    return {k: v for k, v in section.items()
            if k not in META_KEYS and k not in SECRET_KEYS}


# keys that change on their own — never trigger an update
VOLATILE_KEYS = {"deviceTime", "channel"}


def diff_payload(current, desired, ignore=()):
    """Return the subset of `desired` whose values differ from `current`.

    Only keys present in `desired` are compared. `ignore` skips keys that
    drift on their own (e.g. deviceTime ticks every second — a stale dump
    timestamp would always look "different" and then fail to set anyway).
    """
    if not isinstance(current, dict):
        return {k: v for k, v in desired.items() if k not in ignore}
    return {k: v for k, v in desired.items()
            if k not in ignore and (k not in current or current[k] != v)}


SET_TO_GET = {s: g for g, s in ENDPOINTS.items() if s}


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------
def probe_device(ip, password, timeout=3):
    """Return (ip, info) if this is a compatible logged-in device, else None."""
    cam = Camera(ip, password, timeout=timeout)
    try:
        info = cam.login()
    except Exception:
        return None
    return cam


def iter_targets(target):
    """Expand target into list of IPs (single IP, hostname, or CIDR)."""
    try:
        net = ipaddress.ip_network(target, strict=False)
        if net.num_addresses > 1:
            return [str(ip) for ip in net.hosts()]
        return [str(net.network_address)]
    except ValueError:
        return [target]


def discover(target, password, workers=32):
    ips = iter_targets(target)
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe_device, ip, password): ip for ip in ips}
        for fut in concurrent.futures.as_completed(futs):
            cam = fut.result()
            if cam:
                found.append(cam)
                print(f"  found {cam.ip}  deviceID={cam.info.get('deviceID')} "
                      f"deyType={cam.info.get('deyType')}", flush=True)
    return sorted(found, key=lambda c: tuple(int(p) for p in c.ip.split('.'))
                  if all(p.isdigit() for p in c.ip.split('.')) else (999,))


# ---------------------------------------------------------------------------
# Dump
# ---------------------------------------------------------------------------
def dump_camera(cam):
    print(f"[{cam.ip}] dumping...")
    dump = {"_meta": {"ip": cam.ip, "hiLogin": cam.info}, "sections": {}}
    for ep in ENDPOINTS:
        try:
            if ep in CHANNELIZED:
                chans = []
                for ch in range(MAX_CHANNEL):
                    r = cam.get(ep, channel=ch)
                    if r.get("code") != 0 or r.get("pic_width", 1) == 0:
                        break
                    chans.append(r)
                dump["sections"][ep] = chans
                print(f"    {ep}: {len(chans)} channel(s)")
            else:
                r = cam.get(ep)
                dump["sections"][ep] = r
                print(f"    {ep}: code={r.get('code')}")
        except Exception as e:
            dump["sections"][ep] = {"_error": str(e)}
            print(f"    {ep}: FAILED ({e})")
    return dump


def cmd_dump(args):
    os.makedirs(args.output, exist_ok=True)
    cams = discover(args.target, args.password)
    if not cams:
        print("No compatible devices found.")
        return 1
    for cam in cams:
        dump = dump_camera(cam)
        path = os.path.join(args.output, f"{cam.ip}.json")
        with open(path, "w") as f:
            json.dump(dump, f, indent=1)
        print(f"[{cam.ip}] -> {path}")
    return 0


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def apply_section(cam, get_ep, set_ep, section, force=False):
    payload = clean_for_apply(section)
    if not isinstance(payload, dict) or "_error" in payload or not payload:
        return "skipped"
    channel = payload.get("channel")
    if not force:
        try:
            current = cam.get(get_ep, channel=channel) if channel is not None \
                else cam.get(get_ep)
            payload = diff_payload(current, payload,
                                   ignore=VOLATILE_KEYS)
            if channel is not None:
                payload["channel"] = channel
        except Exception as e:
            return f"FAILED (read: {e})"
        if not payload or set(payload) <= {"channel"}:
            return "unchanged"
        if get_ep == "getDeviceTime":
            # firmware requires deviceTime; send fresh time, not the stale dump
            payload["deviceTime"] = int(time.time())
    try:
        r = cam.set(set_ep, payload)
    except Exception as e:
        return f"FAILED ({e})"
    n = len(payload) - (1 if "channel" in payload else 0)
    if r.get("code") == 0:
        return f"updated ({n} field{'s' if n != 1 else ''})"
    return f"code={r.get('code')} {r.get('message','')}"


def apply_dump(cam, dump, skip_network, force=False):
    dtype_src = dump.get("_meta", {}).get("hiLogin", {})
    if dtype_src.get("deyType") and dtype_src["deyType"] != cam.info.get("deyType"):
        print(f"[{cam.ip}] SKIP: device type mismatch "
              f"({cam.info.get('deyType')} != {dtype_src['deyType']})")
        return
    sections = dump.get("sections", {})
    for get_ep, set_ep in ENDPOINTS.items():
        if set_ep is None or get_ep not in sections:
            continue
        if skip_network and get_ep in ("getWiredNetwork", "getWifiConfig"):
            print(f"    {set_ep}: skipped (--skip-network)")
            continue
        sec = sections[get_ep]
        if get_ep in CHANNELIZED and isinstance(sec, list):
            for i, chsec in enumerate(sec):
                if isinstance(chsec, dict):
                    chsec = dict(chsec, channel=i)
                print(f"    {set_ep}[ch{i}]: "
                      f"{apply_section(cam, get_ep, set_ep, chsec, force)}")
        else:
            print(f"    {set_ep}: "
                  f"{apply_section(cam, get_ep, set_ep, sec, force)}")


def cmd_apply(args):
    with open(args.input) as f:
        dump = json.load(f)
    cams = discover(args.target, args.password)
    if not cams:
        print("No compatible devices found.")
        return 1
    for cam in cams:
        print(f"[{cam.ip}] applying...")
        apply_dump(cam, dump, args.skip_network, args.force)
    return 0


def cmd_mac(args):
    cams = discover(args.target, args.password)
    for cam in cams:
        print(f"{cam.ip:16} {cam.info.get('device_mac')}  "
              f"{cam.info.get('deviceID','')}")
    print(f"\n{len(cams)} device(s)")
    return 0


# ---------------------------------------------------------------------------
def cmd_scan(args):
    cams = discover(args.target, args.password)
    print(f"\n{len(cams)} compatible device(s) found.")
    return 0


# ---------------------------------------------------------------------------
# List / set individual properties
# ---------------------------------------------------------------------------
def parse_value(s):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


def cmd_list(args):
    """List endpoints, or properties of one section on each target device."""
    cams = discover(args.target, args.password)
    if not cams:
        print("No compatible devices found.")
        return 1
    if not args.endpoint:
        print(f"{'get-endpoint':28} {'set-endpoint':28} description")
        for g, s in ENDPOINTS.items():
            print(f"{g:28} {s or '-':28}")
        return 0
    ep = args.endpoint
    if ep not in ENDPOINTS:
        print(f"unknown endpoint {ep!r}; run `list <ip>` without endpoint to see all")
        return 1
    for cam in cams:
        chans = range(MAX_CHANNEL) if ep in CHANNELIZED else [None]
        for ch in chans:
            try:
                r = cam.get(ep, channel=ch)
            except Exception as e:
                print(f"[{cam.ip}] {ep} ch={ch}: FAILED {e}")
                continue
            bad = (r.get("code") not in (0, None)
                   or (ch is not None and r.get("pic_width", 1) == 0))
            if bad:
                if ch is None:
                    print(f"[{cam.ip}] {ep}: code={r.get('code')} "
                          f"{r.get('message','')}")
                break
            label = f"[{cam.ip}] {ep}" + (f" ch{ch}" if ch is not None else "")
            print(label)
            for k, v in r.items():
                if k not in META_KEYS:
                    print(f"    {k} = {json.dumps(v)}")
    return 0


def cmd_set(args):
    """Set individual properties: set <target> <endpoint> key=val [key=val...]"""
    ep = args.endpoint
    if ep in ENDPOINTS:                       # user gave get-name
        get_ep, set_ep = ep, ENDPOINTS[ep]
    elif ep in SET_TO_GET:                    # user gave set-name
        get_ep, set_ep = SET_TO_GET[ep], ep
    else:
        get_ep, set_ep = None, None
    if not set_ep:
        print(f"{ep!r} is read-only or unknown; valid set-endpoints:")
        print("  " + " ".join(sorted(s for s in ENDPOINTS.values() if s)))
        return 1
    payload = {}
    for kv in args.pairs:
        if "=" not in kv:
            print(f"ignoring malformed pair {kv!r} (expected key=value)")
            continue
        k, v = kv.split("=", 1)
        payload[k] = parse_value(v)
    if not payload:
        print("nothing to set")
        return 1
    if args.channel is not None:
        payload["channel"] = args.channel
    cams = discover(args.target, args.password)
    if not cams:
        print("No compatible devices found.")
        return 1
    for cam in cams:
        to_send = payload
        if not args.force and get_ep:
            try:
                current = cam.get(get_ep, channel=args.channel)
                to_send = diff_payload(current, payload)
                if args.channel is not None:
                    to_send["channel"] = args.channel
            except Exception as e:
                print(f"[{cam.ip}] {set_ep}: FAILED (read: {e})")
                continue
            if set(to_send) <= {"channel"}:
                print(f"[{cam.ip}] {set_ep}: unchanged")
                continue
        try:
            r = cam.set(set_ep, to_send)
            print(f"[{cam.ip}] {set_ep} {to_send} -> code={r.get('code')} "
                  f"{r.get('message','')}")
        except Exception as e:
            print(f"[{cam.ip}] {set_ep}: FAILED {e}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("scan", cmd_scan), ("mac", cmd_mac), ("dump", cmd_dump),
                     ("apply", cmd_apply), ("list", cmd_list), ("set", cmd_set)):
        p = sub.add_parser(name)
        p.add_argument("target", help="IP, hostname, or CIDR (e.g. 10.91.1.0/24)")
        p.add_argument("--password", "-p", default="123456")
        if name == "dump":
            p.add_argument("-o", "--output", default="dumps")
        if name == "apply":
            p.add_argument("-i", "--input", required=True, help="dump JSON file")
            p.add_argument("--skip-network", action="store_true",
                           help="don't push wired/wifi network settings")
            p.add_argument("--force", action="store_true",
                           help="send full sections without diff-checking")
        if name == "list":
            p.add_argument("endpoint", nargs="?",
                           help="e.g. getVencConf (omit to list all endpoints)")
        if name == "set":
            p.add_argument("endpoint",
                           help="get- or set-endpoint name, e.g. setRtspConf")
            p.add_argument("pairs", nargs="+", metavar="key=value",
                           help="properties, values parsed as JSON "
                                "(e.g. rtsp_port=554 auth=1)")
            p.add_argument("--channel", type=int,
                           help="stream channel for channelized endpoints")
            p.add_argument("--force", action="store_true",
                           help="send without reading current state first")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
