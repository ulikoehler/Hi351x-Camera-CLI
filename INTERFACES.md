# Camera Device Interfaces — Investigation Report

Tested device: `http://192.168.1.168/` (credentials: admin / 123456)
Identified as an "IPCamera" firmware: `22.010.30.6_MAIN_V44`, platform `v9.6.1`
(hf* / "hi3510" family — HiSilicon-based camera, ONVIF stack `hfonvif/1.0`).

**SoC: HiSilicon Hi3516CV610** (confirmed via vendor database; not readable
through any exposed network interface — neither `/action/*`, ONVIF
`GetDeviceInformation`, RTSP headers nor web assets reveal the chip name.
Entry-level 5 MP / H.265 SoC, matching the observed 2592×1944 max resolution.)

## Open ports

| Port | Service | Notes |
|------|---------|-------|
| 80   | HTTP    | Web UI (Vue.js SPA) + `/action/*` JSON API |
| 554  | RTSP    | Video streams. Main: `rtsp://<ip>:554/11`, Sub: `rtsp://<ip>:554/12` (typical for this family) |
| 8000 | TCP     | Private SDK/talk protocol (no banner, likely proprietary binary protocol) |
| 8080 | ONVIF   | `hfonvif/1.0` SOAP service |

## 1. HTTP JSON API (`/action/*`) — primary configuration interface

### Authentication

`POST /action/hiLogin` with JSON body `{"passwd": "<md5 of plaintext password>"}`.

Response (success):
```json
{"userRole":3,"failedAttempts":3,"code":0,"deviceID":"H0100011A120100011893",
 "device_mac":"bc-07-18-03-32-55","device_ip":"192.168.1.168","TempType":0,
 "deyType":"H2S02P100000"}
```
`code: 0` = success. Session is tracked server-side (no cookie returned; appears
bound to client IP). Always call `hiLogin` before other endpoints.

### Request/response convention

- All endpoints are `POST` with `Content-Type: application/json`.
- `get*` endpoints accept `{}` (or `{"channel": N}` for per-stream endpoints)
  and return the full config object plus metadata fields:
  `code`, `device_mac`, `deviceID`, `device_id`, `device_ip`, `log`, `sign_tby`.
- `set*` endpoints accept **partial** JSON objects — only the fields to change.
  Response: `{"code":0,...}` on success. Non-zero `code` = failure.
- Response-only metadata fields should be stripped before re-applying.

### Endpoint inventory

| Get endpoint | Set endpoint | Description |
|---|---|---|
| getSysConfig | setSysConfig | System: version, dev_name, language, webPort |
| getWiredNetwork | setWiredNetwork | IP, DHCP, gateway, DNS, ONVIF ports/flags |
| getDeviceTime | setTime | Time, NTP enable/server, timezone, PTP |
| getRtspConf | setRtspConf | RTSP enable/auth/port, per-stream audio, MTU |
| getVencConf | setVencConf | Video encoder per `channel` (0=main,1=sub): resolution, fps, GOP, bitrate, codec. `encode_type`: 1=H.264, 3=MJPEG, 5=H.265, 6=H.265+. `encode_profile`: 0/1/2 (complexity; 2 only for H.264). `rc_mode`: 0=CBR, 1=VBR |
| getAencConf | setAencConf | Audio encoder: volume, sample rate, codec, talk port |
| getOsdConf | setOsdConf | OSD: date/time/title overlays, font, position |
| getOsdBMPLogo / getOsdBMPLogo_new | setOsdBMPLogo / _new | OSD bitmap logo |
| getImageAdjustment | setImageAdjustment | Image: brightness, contrast, saturation, exposure, AWB… |
| getImageAdjustmentEx | setImageAdjustmentEx | Extended image adjustment |
| getViMask | setViMask | Privacy masks |
| getMotionDetConf | setMotionDetConf | Motion detection: sensitivity, areas, schedule, alarm actions |
| getNetAlarmConf | setNetAlarmConf | Network/video-loss style alarm actions |
| getViLoseAlarmConf | setViLoseAlarmConf | Video-loss alarm |
| getODAlarmConf | setODAlarmConf | Occlusion/detection alarm |
| getAlarmInConf | setAlarmInConf | Physical alarm inputs |
| getAlarmStaus | — | Current alarm status |
| getAlarmLog | — | Alarm log |
| getMailConf | setMailConf | SMTP / alarm e-mail |
| getFtpTest → ftpTest | ftpTest | FTP test action |
| getRtmpConf | setRtmpConf | RTMP push config |
| getRtmpStatus | — | RTMP push status |
| getRtpConf | setRtpConf | RTP/stream transport |
| getTcpTransfer | setTcpTransfer | TCP transfer settings |
| getGb28181 | setGb28181 | GB/T-28181 SIP platform registration |
| getGb28181Status | setGb28181Register / setGb28181Logout | Status / register / logout actions |
| getWifiConfig | setWifiConfig | Wi-Fi client + AP hotspot settings |
| getWifiStatus | setWifiScan | Wi-Fi status / scan action |
| Get4GConfig | Set4GConfig | 4G modem params |
| getRecSchedule | setRecSchedule | Recording schedule (7 days × 4 slots) |
| getStorageInfo / getStorageRule | setStorageRule | SD/NAS storage status & policy |
| getScheSnap | setScheSnap | Scheduled snapshot |
| getPeripheralConf | setPeripheralConf | RS485/PTZ serial, USB, zoom, temperature |
| getPTZBrush / getPtzTrack | setPTZBrush / setPtzTrack | PTZ brush / auto-tracking |
| setPtzControl / setPtzControl_smartZoom | — | PTZ movement actions |
| getRXControl | setRXControl | (returns -1 on this model — unsupported) |
| getIVSConf | setIVSConf | Intelligent video analysis (209 = unsupported here) |
| getSmartAudioConf | setSmartAudioConf | (-1 unsupported) |
| GetFaceConfig | SetFaceConfig | Face detection config |
| getPedelecConf | setPedelecConf | (209 unsupported) |
| getIllegalParkingConf | setIllegalParkingConf | Illegal-parking detection |
| getLeaveThePostConf | setLeaveThePostConf | Leave-post detection |
| getRegInvConf | setRegInvConf | Region invasion detection |
| getWiegandConfig | setWiegandConfig | Wiegand card interface |
| getWgcard | — | Wiegand card list |
| getUserList / getUserInfo / getUserGroup | addUser, updateUser, deleteUser, delUserList, setUserGroup, setPasswd | User management (getUserInfo needs `user_id`) |
| getPlatformServer | setPlatformServer | Cloud/platform server |
| getPlatformConfFile / _webrtc | — | Platform config file |
| getUUIDConf | — | Device UUID |
| getDevConf_jy | setDevConf_jy | Vendor-specific (code 1 here) |
| getBTConf, getBTList_*, getBTConf_connect | setBTConf* | Bluetooth (audio doorbell models) |
| getOutputVolume | setOutputVolume | Speaker volume |
| getDefaultParam | setDefaultParam, delDefaultParam | Factory-default param management |
| getRebootConf | setRebootConf | Scheduled auto-reboot |
| getPlaybackList, getPlaybackTime, openRecordFile, playRecordFile, seekRecordFile, getRecordList, getRecordVideoInfo, setPlaySpeed | — | SD playback control |
| getLogTar | — | Download logs |
| getNetAlarmConf | setNetAlarmConf | Network disconnect alarm |
| — | audioUpload, audioPlayFile_base64 | Alarm audio file upload/play |
| — | uploadFile, fwInfo, fwUpgrade, fwUpgradeProgress, driverUpgrade, driverUpgrade_percentage | Firmware/driver upgrade |
| — | diskFormat | Format SD |
| — | reset, restart | Factory reset / reboot |
| — | eMailTest, pingIp | Utilities |
| — | liveshow | Live view session |

### Multi-channel (stream) endpoints

`getVencConf` accepts `{"channel": N}`. This unit: channel 0 = main (up to
2592×1944), channel 1 = sub (704×576), channel ≥2 returns empty (all zeros).
Apply iterates until a channel returns `pic_width == 0`.

## 2. Legacy CGI — `/cgi-bin/hi3510/param.cgi`

Responds on port 80. Classic XM-style `?cmd=...` commands all return
`error command` on this firmware — the command set was replaced by `/action/*`.
Kept only for fingerprinting (200 vs 404 distinguishes this family).

## 3. ONVIF — port 8080 (`hfonvif/1.0`)

Standard ONVIF SOAP (GetCapabilities / GetProfiles etc.). `getWiredNetwork`
exposes `onvifPort` (8080), `onvifAuthEnable`, `onvifHttpsEnable`,
`onvifRtspOverHttpsEnable`. Usable as secondary fingerprint + for media config.

## 4. RTSP — port 554

`getRtspConf`: `enable`, `auth` (0 = no auth), `rtsp_port`, `audio_main/sub/thr`,
`mtu`. Typical paths on this family: `/11` main, `/12` sub, `/13` third stream.

## 5. Port 8000

Private binary protocol (SDK/p2p/talk). No banner; not needed for config dump/apply.

## 6. Device fingerprinting (for network scans)

1. `GET http://<ip>/` → `302 → /index.html` serving the "WEB SERVICE" Vue SPA.
2. `POST /action/hiLogin` with md5(password) → JSON containing `deviceID`,
   `device_mac`, `code`. `code==0` ⇒ confirmed compatible device.
3. `hiLogin` also returns `deyType` (board type, e.g. `H2S02P100000`) — dump/apply
   is only guaranteed for identical `deyType`/`dev_type` (`getSysConfig`).
   Other device types should be skipped (as requested).

## 7. Apply semantics & caveats

- `set*` accepts partial objects; safest to post the dumped object minus
  metadata keys (`code,message,deviceID,device_id,device_mac,device_ip,log,
  sign_tby,userRole,failedAttempts,TempType,deyType,pixel_list,max_framerate,
  validSupport,devs`).
- Changing `getWiredNetwork` (IP/DHCP) or `webPort` may move the device —
  apply last / optional.
- Some `set*` trigger reboot (firmware reports it in UI as "loading_reboot").
- Do not apply password fields (`gb_passwd`, `AP_Passwd`, `smtp_passw`,
  wifi `passwd`, user passwords) across devices unless intended.
- `setPasswd`, `reset`, `restart`, `fwUpgrade`, `diskFormat` are destructive —
  never called by dump/apply.
