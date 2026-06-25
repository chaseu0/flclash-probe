#!/usr/bin/env python3
"""FlClash proxy exit probe: IP, fraud score, VPN, type, service reachability."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

APP_DIR = Path.home() / "Library/Application Support/flclash-probe"
STATE_FILE = APP_DIR / "state.json"
REPORT_JSON = APP_DIR / "report.json"
REPORT_HTML = APP_DIR / "report.html"
CONFIG_FILE = APP_DIR / "config.json"
SERVICES_FILE = APP_DIR / "services.json"
LOG_FILE = APP_DIR / "probe.log"
MIHOMO_BIN = APP_DIR / "bin" / "mihomo"
FLCLASH_DIR = Path.home() / "Library/Application Support/com.follow.clash"
PLIST_PATH = Path.home() / "Library/Preferences/com.follow.clash.plist"
PROFILE_ID = "1782393408607"
PROFILE_PATH = FLCLASH_DIR / "profiles" / f"{PROFILE_ID}.yaml"
CONFIG_PATH = FLCLASH_DIR / "config.yaml"

FLAG_RE = re.compile(r"^((?:[\U0001F1E6-\U0001F1FF]{2}\s*)+)")
PROBE_SUFFIX_RE = re.compile(
    r"\|[^|]+\|F\d+\|[V·?]\|[RHM·?](?:\|S[\w.|ms]+)?$"
)
FRAUD_FROM_NAME_RE = re.compile(r"\|F(\d+)\|")
SERVICE_FROM_NAME_RE = re.compile(r"\|S(\d+)")
AVG_MS_FROM_NAME_RE = re.compile(r"\|(\d+)ms(?:\||$)|\|S\d+\|(\d+)ms")
INFO_RE = re.compile(r"^(剩余流量|距离下次|套餐到期)")
PREFIX_PROXY_NAMES = frozenset({"自动选择", "故障转移", "DIRECT", "REJECT", "GLOBAL"})
PROVIDERS = {
    "ip-api": "ip-api.com",
    "ipwho": "ipwho.is",
    "ipinfo": "ipinfo.io",
}
REGION_FLAGS = {
    "HK": "🇭🇰", "TW": "🇹🇼", "JP": "🇯🇵", "US": "🇺🇸", "SG": "🇸🇬",
    "KR": "🇰🇷", "VN": "🇻🇳", "DE": "🇩🇪", "GB": "🇬🇧", "FR": "🇫🇷",
    "CA": "🇨🇦", "AU": "🇦🇺", "IN": "🇮🇳", "RU": "🇷🇺", "NL": "🇳🇱",
    "TH": "🇹🇭", "MY": "🇲🇾", "ID": "🇮🇩", "PH": "🇵🇭", "BR": "🇧🇷",
    "AE": "🇦🇪", "SA": "🇸🇦", "IL": "🇮🇱", "IT": "🇮🇹", "ES": "🇪🇸",
    "SE": "🇸🇪", "CH": "🇨🇭", "UA": "🇺🇦", "ZA": "🇿🇦", "CO": "🇨🇴",
    "CL": "🇨🇱", "MA": "🇲🇦", "TR": "🇹🇷", "PL": "🇵🇱",
}
IP_API = "http://ip-api.com/json/?fields=status,query,proxy,hosting,mobile,isp,org"
IPWHO = "https://api.ipwho.is/?security=1"
IPINFO = "https://ipinfo.io/json"
HOSTING_ORG_KW = (
    "amazon", "google", "oracle", "microsoft", "digitalocean", "linode", "vultr",
    "hetzner", "ovh", "cloud", "hosting", "datacenter", "g-core", "akamai", "alibaba",
    "tencent", "huawei", "contabo", "leaseweb",
)
EC_URL = "http://127.0.0.1:9090"
PROBE_TIMEOUT = 18
SERVICE_TIMEOUT = 8
MIHOMO_START_WAIT = 2.5
DEFAULT_SERVICES = [
    {"name": "Google", "url": "https://www.google.com/generate_204", "weight": 1.0, "enabled": True},
    {"name": "YouTube", "url": "https://www.youtube.com/generate_204", "weight": 1.0, "enabled": True},
    {"name": "Facebook", "url": "https://www.facebook.com/robots.txt", "weight": 1.0, "enabled": True},
    {"name": "Netflix", "url": "https://www.netflix.com/generate_204", "weight": 1.0, "enabled": True},
    {"name": "ChatGPT", "url": "https://chatgpt.com/", "weight": 1.0, "enabled": True},
    {"name": "TikTok", "url": "https://www.tiktok.com/robots.txt", "weight": 1.0, "enabled": True},
]


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"nodes": {}, "last_run": None, "status": "idle", "progress": ""}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_services() -> list[dict[str, Any]]:
    if SERVICES_FILE.exists():
        try:
            data = json.loads(SERVICES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("services"), list):
                return data["services"]
        except json.JSONDecodeError:
            pass
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(cfg.get("services"), list):
                return cfg["services"]
        except json.JSONDecodeError:
            pass
    return [dict(s) for s in DEFAULT_SERVICES]


def save_services(services: list[dict[str, Any]]) -> None:
    SERVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERVICES_FILE.write_text(
        json.dumps({"services": services}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {"provider": "ip-api"}
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except json.JSONDecodeError:
            pass
    if cfg.get("provider") not in PROVIDERS:
        cfg["provider"] = "ip-api"
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def fraud_from_name(name: str) -> int | None:
    m = FRAUD_FROM_NAME_RE.search(name)
    return int(m.group(1)) if m else None


def service_from_name(name: str) -> int | None:
    m = SERVICE_FROM_NAME_RE.search(name)
    return int(m.group(1)) if m else None


def avg_ms_from_name(name: str) -> float | None:
    m = AVG_MS_FROM_NAME_RE.search(name)
    if not m:
        return None
    val = m.group(1) or m.group(2)
    return float(val) if val else None


def is_failed_probe_name(name: str) -> bool:
    if "|err|" in name:
        return True
    if re.search(r"\|\?\|F99\|", name):
        return True
    if name.endswith("F99|?|?"):
        return True
    if re.search(r"\|F99\|", name):
        return True
    return False


def proxy_sort_key(name: str, results: dict[str, Any] | None = None) -> tuple[int, int, int, float, str]:
    """Lower tuple = better. Failures last. Then F ASC, S DESC, avg_ms ASC."""
    info = (results or {}).get(name) or {}
    if info.get("ok") is False or is_failed_probe_name(name):
        return (2, 999, 0, 999999.0, name)
    fraud = info.get("fraud")
    if fraud is None:
        fraud = fraud_from_name(name)
    if fraud is None:
        fraud = 50
    if fraud >= 99 or is_failed_probe_name(name):
        return (2, 999, 0, 999999.0, name)
    service = info.get("service_score")
    if service is None:
        service = service_from_name(name)
    if service is None:
        service = 0
    avg_ms = info.get("service_avg_ms")
    if avg_ms is None:
        avg_ms = avg_ms_from_name(name)
    if avg_ms is None:
        avg_ms = 999999.0
    return (0, int(fraud), -int(service), float(avg_ms), name)


def is_prefix_proxy(name: str) -> bool:
    if name in PREFIX_PROXY_NAMES:
        return True
    if INFO_RE.match(name):
        return True
    if not FRAUD_FROM_NAME_RE.search(name):
        return True
    return False


def sort_proxy_list(proxies: list[str], results: dict[str, Any] | None = None) -> list[str]:
    prefix = [n for n in proxies if is_prefix_proxy(n)]
    sortable = [n for n in proxies if not is_prefix_proxy(n)]
    sortable.sort(key=lambda n: proxy_sort_key(n, results))
    return prefix + sortable


def sort_profile_groups(profile: dict[str, Any], results: dict[str, Any] | None = None) -> None:
    for g in profile.get("proxy-groups") or []:
        if not g.get("proxies"):
            continue
        g["proxies"] = sort_proxy_list(g["proxies"], results)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def strip_probe_suffix(name: str) -> str:
    clean = name
    for _ in range(4):
        nxt = PROBE_SUFFIX_RE.sub("", clean)
        if nxt == clean:
            break
        clean = nxt
    return clean.strip()


def region_code_from_label(label: str) -> str | None:
    compact = re.sub(r"[^A-Za-z0-9]", "", label.upper())
    m = re.match(r"^([A-Z]{2})\d*", compact)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Z]{2})$", compact)
    return m.group(1) if m else None


def parse_display_parts(name: str) -> tuple[str, str]:
    """Return (flag_prefix, base_label) preserving emoji when present."""
    clean = strip_probe_suffix(name)
    m = FLAG_RE.match(clean)
    if m:
        flag = m.group(1)
        rest = clean[m.end():].strip()
    else:
        flag = ""
        rest = clean
    rest = re.sub(r"^[\s|]+", "", rest)
    head = rest.split("|")[0].strip() if "|" in rest else rest.strip()
    m = re.match(r"^([A-Za-z]{2}\d{0,3})$", head.replace(" ", ""))
    if m:
        base = m.group(1).upper()
    else:
        compact = re.sub(r"\s*\|\s*", " ", rest).strip()
        m = re.search(r"\b([A-Z]{2}\d{0,3})\b", compact.upper())
        base = m.group(1) if m else (head[:12] or "node")
    if not flag:
        code = region_code_from_label(base)
        if code and code in REGION_FLAGS:
            flag = REGION_FLAGS[code] + " "
    return flag, base


def fraud_score(proxy: bool, vpn: bool, hosting: bool, mobile: bool) -> int:
    score = 8
    if vpn or proxy:
        score += 42
    if hosting:
        score += 28
    if mobile:
        score += 18
    return min(score, 99)


def build_fraud_detail(
    provider: str, raw: dict[str, Any], info: dict[str, Any], score: int,
) -> dict[str, Any]:
    proxy = bool(info.get("proxy"))
    vpn = bool(info.get("vpn"))
    hosting = bool(info.get("hosting"))
    mobile = bool(info.get("mobile"))
    breakdown = {
        "base": 8,
        "proxy_or_vpn": 42 if (vpn or proxy) else 0,
        "hosting": 28 if hosting else 0,
        "mobile": 18 if mobile else 0,
    }
    api_urls = {
        "ip-api": IP_API,
        "ipwho": IPWHO,
        "ipinfo": IPINFO,
    }
    field_map = {
        "ip-api": ["query", "proxy", "hosting", "mobile", "isp", "org", "status"],
        "ipwho": ["ip", "security.proxy", "security.vpn", "security.hosting", "connection.type", "connection.isp"],
        "ipinfo": ["ip", "org", "privacy.proxy", "privacy.vpn", "privacy.hosting"],
    }
    return {
        "provider": provider,
        "provider_label": PROVIDERS.get(provider, provider),
        "api_url": api_urls.get(provider, ""),
        "raw_response": raw,
        "parsed_fields": {k: info.get(k) for k in ("ip", "proxy", "vpn", "hosting", "mobile", "isp")},
        "fields_used": field_map.get(provider, []),
        "formula": "F = min(99, 8 + 42*(proxy|vpn) + 28*hosting + 18*mobile)",
        "breakdown": breakdown,
        "computed_sum": sum(breakdown.values()),
        "fraud_score": score,
    }


def conn_type(hosting: bool, mobile: bool) -> str:
    if hosting:
        return "H"
    if mobile:
        return "M"
    return "R"


def format_service_score(score: int, avg_ms: float | None = None) -> str:
    if score <= 0:
        return "S0"
    if avg_ms is not None:
        if avg_ms >= 3000:
            return f"S{score}|{avg_ms / 1000:.1f}s"
        return f"S{score}|{int(round(avg_ms))}ms"
    return f"S{score}"


def build_probe_name(
    flag: str, base: str, ip: str, fraud: int, vpn: bool, ctype: str, service_score: int,
    avg_ms: float | None = None,
) -> str:
    v = "V" if vpn else "·"
    ip_disp = ip if ip and ip != "?" else "?"
    s_tag = format_service_score(service_score, avg_ms)
    prefix = flag if flag.endswith(" ") or not flag else flag + " "
    return f"{prefix}{base}|{ip_disp}|F{fraud}|{v}|{ctype}|{s_tag}"


def http_get(url: str, proxy: str | None = None, timeout: float = 12) -> dict[str, Any]:
    handlers: list[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "flclash-probe/2.0"})
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def http_probe(url: str, proxy: str, timeout: float = SERVICE_TIMEOUT) -> tuple[bool, float]:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})]
    opener = urllib.request.build_opener(*handlers)
    start = time.perf_counter()
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(
                url, method=method, headers={"User-Agent": "flclash-probe/2.0"},
            )
            with opener.open(req, timeout=timeout) as resp:
                if 200 <= resp.status < 500:
                    ms = (time.perf_counter() - start) * 1000
                    return True, ms
        except Exception:
            continue
    return False, timeout * 1000


def compute_service_score(results: list[dict[str, Any]]) -> tuple[int, float | None]:
    if not results:
        return 0, None
    if any(not r.get("ok") for r in results):
        return 0, None
    total_w = sum(float(r.get("weight", 1.0)) for r in results)
    if total_w <= 0:
        return 0, None
    avg_ms = sum(float(r["weight"]) * float(r["ms"]) for r in results) / total_w
    score = max(0, min(99, int(99 - avg_ms * 99 / (SERVICE_TIMEOUT * 1000))))
    return score, avg_ms


def probe_services(proxy_url: str, services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enabled = [s for s in services if s.get("enabled", True)]
    if not enabled:
        return []
    out: list[dict[str, Any]] = []
    workers = min(6, len(enabled))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(http_probe, s["url"], proxy_url, SERVICE_TIMEOUT): s
            for s in enabled
        }
        for fut in as_completed(futures):
            svc = futures[fut]
            try:
                ok, ms = fut.result()
            except Exception:
                ok, ms = False, SERVICE_TIMEOUT * 1000
            out.append({
                "name": svc.get("name", "?"),
                "url": svc.get("url", ""),
                "weight": float(svc.get("weight", 1.0)),
                "ok": ok,
                "ms": round(ms, 1),
            })
    return out


def fetch_ip_api(proxy_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data = http_get(IP_API, proxy=proxy_url, timeout=PROBE_TIMEOUT)
    if data.get("status") != "success":
        raise ValueError("ip-api status != success")
    info = {
        "ip": data.get("query", "?"),
        "proxy": bool(data.get("proxy")),
        "vpn": bool(data.get("proxy")),
        "hosting": bool(data.get("hosting")),
        "mobile": bool(data.get("mobile")),
        "isp": data.get("isp") or data.get("org") or "",
    }
    return info, data


def fetch_ipwho(proxy_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    w = http_get(IPWHO, proxy=proxy_url, timeout=PROBE_TIMEOUT)
    if not w.get("success", True) and w.get("ip") is None:
        raise ValueError("ipwho request failed")
    sec = w.get("security") or {}
    conn = w.get("connection") or {}
    info = {
        "ip": w.get("ip", "?"),
        "proxy": bool(sec.get("proxy")),
        "vpn": bool(sec.get("vpn")),
        "hosting": bool(sec.get("hosting")),
        "mobile": bool(conn.get("type") == "mobile"),
        "isp": conn.get("isp") or w.get("isp") or "",
    }
    return info, w


def fetch_ipinfo(proxy_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = load_config()
    token = os.environ.get("IPINFO_TOKEN") or cfg.get("ipinfo_token") or ""
    url = IPINFO if not token else f"{IPINFO}?token={urllib.parse.quote(token)}"
    data = http_get(url, proxy=proxy_url, timeout=PROBE_TIMEOUT)
    org = (data.get("org") or "").lower()
    hosting = any(k in org for k in HOSTING_ORG_KW)
    mobile = any(k in org for k in ("mobile", "cellular", "wireless", "telecom"))
    privacy = data.get("privacy") or {}
    proxy = bool(privacy.get("proxy"))
    vpn = bool(privacy.get("vpn"))
    if token and privacy.get("hosting") is not None:
        hosting = bool(privacy.get("hosting"))
    info = {
        "ip": data.get("ip", "?"),
        "proxy": proxy,
        "vpn": vpn or proxy,
        "hosting": hosting,
        "mobile": mobile,
        "isp": data.get("org") or "",
    }
    return info, data


def fetch_ip_info(proxy_url: str, provider: str | None = None) -> dict[str, Any]:
    provider = provider or load_config().get("provider", "ip-api")
    fetchers = {
        "ip-api": fetch_ip_api,
        "ipwho": fetch_ipwho,
        "ipinfo": fetch_ipinfo,
    }
    fn = fetchers.get(provider, fetch_ip_api)
    try:
        info, raw = fn(proxy_url)
        if info.get("ip") and info["ip"] != "?":
            return {**info, "_provider": provider, "_raw": raw}
    except Exception as exc:
        log(f"{provider} failed via {proxy_url}: {exc}")
    return {
        "ip": "?", "proxy": False, "vpn": False, "hosting": False, "mobile": False,
        "isp": "", "_provider": provider, "_raw": {},
    }


def probe_one_proxy(
    proxy: dict[str, Any], mihomo: Path, services: list[dict[str, Any]],
) -> dict[str, Any]:
    port = free_port()
    work = Path(tempfile.mkdtemp(prefix="flclash-probe-"))
    cfg_path = work / "config.yaml"
    pname = "P"
    minimal = {
        "mixed-port": port,
        "mode": "rule",
        "log-level": "silent",
        "proxies": [{k: v for k, v in proxy.items() if k != "name"} | {"name": pname}],
        "proxy-groups": [{"name": "G", "type": "select", "proxies": [pname]}],
        "rules": ["MATCH,G"],
    }
    cfg_path.write_text(yaml.dump(minimal, allow_unicode=True, sort_keys=False), encoding="utf-8")
    proc = subprocess.Popen(
        [str(mihomo), "-d", str(work), "-f", str(cfg_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proxy_url = f"http://127.0.0.1:{port}"
    try:
        time.sleep(MIHOMO_START_WAIT)
        info = fetch_ip_info(proxy_url, provider=load_config().get("provider", "ip-api"))
        vpn = info["vpn"] or info["proxy"]
        score = fraud_score(info["proxy"], vpn, info["hosting"], info["mobile"])
        ctype = conn_type(info["hosting"], info["mobile"])
        provider = info.get("_provider", load_config().get("provider", "ip-api"))
        fraud_detail = build_fraud_detail(provider, info.get("_raw", {}), info, score)
        svc_results = probe_services(proxy_url, services)
        svc_score, avg_ms = compute_service_score(svc_results)
        ip_ok = info["ip"] != "?"
        return {
            "ip": info["ip"],
            "fraud": score,
            "fraud_detail": fraud_detail,
            "vpn": vpn,
            "type": ctype,
            "isp": info.get("isp", ""),
            "service_score": svc_score,
            "service_avg_ms": round(avg_ms, 1) if avg_ms is not None else None,
            "services": svc_results,
            "ok": ip_ok,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)


def is_real_proxy(p: dict[str, Any]) -> bool:
    name = p.get("name", "")
    if INFO_RE.match(name):
        return False
    if p.get("type") in {"direct", "reject", "dns", "block"}:
        return False
    return bool(p.get("server"))


def load_profile() -> dict[str, Any]:
    with PROFILE_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_profile(data: dict[str, Any]) -> None:
    backup = PROFILE_PATH.with_suffix(".yaml.bak")
    if not backup.exists():
        shutil.copy2(PROFILE_PATH, backup)
    PROFILE_PATH.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def sync_config_yaml(name_map: dict[str, str], results: dict[str, Any] | None = None) -> None:
    if not CONFIG_PATH.exists():
        return
    with CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    changed = False
    for p in cfg.get("proxies") or []:
        old = p.get("name")
        if old in name_map:
            p["name"] = name_map[old]
            changed = True
    for g in cfg.get("proxy-groups") or []:
        if not g.get("proxies"):
            continue
        g["proxies"] = sort_proxy_list([name_map.get(n, n) for n in g["proxies"]], results)
        changed = True
    if changed:
        CONFIG_PATH.write_text(yaml.dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def enable_external_controller() -> None:
    if not PLIST_PATH.exists():
        return
    with PLIST_PATH.open("rb") as f:
        pl = plistlib.load(f)
    cfg = json.loads(pl["flutter.config"])
    patch = cfg.setdefault("patchClashConfig", {})
    if patch.get("external-controller") != "127.0.0.1:9090":
        patch["external-controller"] = "127.0.0.1:9090"
        pl["flutter.config"] = json.dumps(cfg, ensure_ascii=False)
        with PLIST_PATH.open("wb") as f:
            plistlib.dump(pl, f)
        log("enabled external-controller in FlClash plist")


def reload_flclash() -> None:
    enable_external_controller()
    for _ in range(6):
        try:
            req = urllib.request.Request(
                f"{EC_URL}/configs?force=true",
                data=b"{}",
                method="PATCH",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 204:
                    log("FlClash config reloaded via API")
                    return
        except Exception:
            time.sleep(1.5)
    try:
        subprocess.run(["killall", "-HUP", "FlClashCore"], check=False, capture_output=True)
        time.sleep(2)
        log("sent HUP to FlClashCore")
    except Exception as exc:
        log(f"reload fallback failed: {exc}")


def run_probe(limit: int | None = None) -> dict[str, Any]:
    if not MIHOMO_BIN.exists():
        raise FileNotFoundError(f"mihomo not found at {MIHOMO_BIN}")
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"profile not found: {PROFILE_PATH}")

    state = load_state()
    cfg = load_config()
    services = load_services()
    provider = cfg.get("provider", "ip-api")
    state["status"] = "running"
    state["progress"] = "starting"
    state["provider"] = provider
    state["services_count"] = sum(1 for s in services if s.get("enabled", True))
    state["started_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    profile = load_profile()
    proxies = profile.get("proxies") or []
    real = [p for p in proxies if is_real_proxy(p)]
    if limit:
        real = real[:limit]

    name_map: dict[str, str] = {}
    results: dict[str, Any] = {}
    total = len(real)

    for idx, proxy in enumerate(real, 1):
        orig_name = proxy["name"]
        flag, base = parse_display_parts(orig_name)
        state["progress"] = f"{idx}/{total} {base}"
        save_state(state)
        log(f"probing {idx}/{total}: {orig_name}")

        try:
            info = probe_one_proxy(proxy, MIHOMO_BIN, services)
            short = build_probe_name(
                flag, base, info["ip"], info["fraud"], info["vpn"], info["type"],
                info["service_score"], info.get("service_avg_ms"),
            )
            results[orig_name] = {
                "base": base, "flag": flag.strip(), "short_name": short, **info,
            }
            name_map[orig_name] = short
            proxy["name"] = short
            svc_ok = sum(1 for s in info.get("services", []) if s.get("ok"))
            svc_total = len(info.get("services", []))
            log(f"  -> {short} (services {svc_ok}/{svc_total})")
        except Exception as exc:
            err_name = build_probe_name(flag, base, "?", 99, False, "?", 0)
            results[orig_name] = {
                "base": base, "flag": flag.strip(), "short_name": err_name,
                "ok": False, "fraud": 99, "service_score": 0, "error": str(exc),
            }
            name_map[orig_name] = err_name
            proxy["name"] = err_name
            log(f"  -> error: {exc}")

    for g in profile.get("proxy-groups") or []:
        if not g.get("proxies"):
            continue
        g["proxies"] = [name_map.get(n, n) for n in g["proxies"]]

    results_by_short: dict[str, Any] = {
        v["short_name"]: v for v in results.values() if v.get("short_name")
    }
    sort_profile_groups(profile, results_by_short)
    write_profile(profile)
    sync_config_yaml(name_map, results_by_short)
    reload_flclash()

    ok_count = sum(1 for r in results.values() if r.get("ok"))
    state["nodes"] = results
    state["status"] = "done"
    state["progress"] = f"{ok_count}/{total} ok"
    state["provider"] = provider
    state["services_count"] = sum(1 for s in services if s.get("enabled", True))
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["summary"] = {
        "total": total,
        "ok": ok_count,
        "sample": list(results.values())[:5],
    }
    save_state(state)
    log(f"done: {ok_count}/{total} ok")
    save_report(state)
    return state


def _esc(text: Any) -> str:
    s = str(text) if text is not None else ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_report_payload(state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or load_state()
    cfg = load_config()
    services = load_services()
    nodes = state.get("nodes") or {}
    sorted_nodes = sorted(
        nodes.items(),
        key=lambda item: proxy_sort_key(
            item[1].get("short_name", item[0]),
            {item[1].get("short_name", item[0]): item[1] for item in nodes.items()},
        ),
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_run": state.get("last_run"),
        "status": state.get("status"),
        "provider": state.get("provider") or cfg.get("provider"),
        "services_config": services,
        "service_timeout_ms": SERVICE_TIMEOUT * 1000,
        "score_formula": "S = max(0, min(99, int(99 - avg_ms * 99 / timeout_ms))); any fail -> S0",
        "sort_order": "F ASC -> S DESC -> avg_ms ASC -> failures last",
        "nodes": [
            {"orig_name": orig, **info}
            for orig, info in sorted_nodes
        ],
        "summary": state.get("summary"),
    }


def save_report(state: dict[str, Any] | None = None) -> Path:
    payload = build_report_payload(state)
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_report_html(payload)
    REPORT_HTML.write_text(html, encoding="utf-8")
    log(f"report saved: {REPORT_HTML}")
    return REPORT_HTML


def render_report_html(payload: dict[str, Any]) -> str:
    nodes = payload.get("nodes") or []
    provider = payload.get("provider", "?")
    gen_at = payload.get("generated_at", "")
    rows = []
    for n in nodes:
        orig = n.get("orig_name", "?")
        short = n.get("short_name", orig)
        flag = n.get("flag", "")
        base = n.get("base", "?")
        label = f"{flag} {base}".strip() if flag else base
        ip = n.get("ip", "?")
        fraud = n.get("fraud", "?")
        svc = n.get("service_score", 0)
        avg = n.get("service_avg_ms")
        avg_disp = f"{int(avg)}ms" if avg is not None else "—"
        ok = n.get("ok", False)
        status = "✅" if ok else "❌"

        svc_rows = ""
        for s in n.get("services") or []:
            s_ok = s.get("ok")
            ms = s.get("ms", "?")
            mark = "OK" if s_ok else "TIMEOUT"
            cls = "ok" if s_ok else "fail"
            svc_rows += (
                f"<tr class='{cls}'><td>{_esc(s.get('name'))}</td>"
                f"<td>{mark}</td><td>{ms}</td><td>{_esc(s.get('url', ''))}</td></tr>"
            )

        fd = n.get("fraud_detail") or {}
        breakdown = fd.get("breakdown") or {}
        bd_lines = "<br>".join(f"{k}: +{v}" for k, v in breakdown.items())
        raw_json = json.dumps(fd.get("raw_response") or {}, ensure_ascii=False, indent=2)
        parsed = json.dumps(fd.get("parsed_fields") or {}, ensure_ascii=False, indent=2)

        rows.append(f"""
<section class="node {'fail' if not ok else ''}">
  <h2>{status} {_esc(label)} <span class="muted">{_esc(short)}</span></h2>
  <div class="meta">
    <span>IP: <b>{_esc(ip)}</b></span>
    <span>F: <b>{fraud}</b></span>
    <span>S: <b>{svc}</b></span>
    <span>平均延时: <b>{avg_disp}</b></span>
    <span>类型: {_esc(n.get('type', '?'))}</span>
    <span>ISP: {_esc(n.get('isp', ''))}</span>
  </div>
  <h3>各网站测速</h3>
  <table><thead><tr><th>服务</th><th>状态</th><th>延时(ms)</th><th>URL</th></tr></thead>
  <tbody>{svc_rows or "<tr><td colspan=4>无数据</td></tr>"}</tbody></table>
  <h3>欺诈分来源 ({_esc(fd.get('provider_label', provider))})</h3>
  <p class="formula">{_esc(fd.get('formula', ''))}</p>
  <p>API: <code>{_esc(fd.get('api_url', ''))}</code></p>
  <p>字段: {_esc(', '.join(fd.get('fields_used') or []))}</p>
  <p>分解: {bd_lines} → F={_esc(fd.get('fraud_score', fraud))}</p>
  <details><summary>解析字段</summary><pre>{_esc(parsed)}</pre></details>
  <details><summary>API 原始返回</summary><pre>{_esc(raw_json)}</pre></details>
</section>""")

    svc_cfg = json.dumps(payload.get("services_config") or [], ensure_ascii=False, indent=2)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlClash Probe 调试报告</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; background: #0f1115; color: #e6e6e6; }}
  h1 {{ font-size: 1.4rem; }}
  .muted {{ color: #888; font-weight: normal; font-size: 0.9rem; }}
  .node {{ background: #1a1d24; border-radius: 10px; padding: 16px; margin: 16px 0; border: 1px solid #2a2f3a; }}
  .node.fail {{ border-color: #5c2a2a; }}
  .meta span {{ margin-right: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ border: 1px solid #333; padding: 6px 8px; text-align: left; }}
  tr.ok td:nth-child(2) {{ color: #6ddb8a; }}
  tr.fail td:nth-child(2) {{ color: #ff7b7b; }}
  pre {{ background: #0b0d11; padding: 10px; overflow: auto; font-size: 0.75rem; border-radius: 6px; }}
  code {{ color: #9cdcfe; }}
  .formula {{ color: #dcdcaa; }}
  details {{ margin: 8px 0; }}
</style></head><body>
<h1>FlClash Probe 调试报告</h1>
<p>生成: {_esc(gen_at)} | 探测: {_esc(payload.get('last_run', ''))} | 检测商: {_esc(provider)} | 状态: {_esc(payload.get('status'))}</p>
<p>排序: {_esc(payload.get('sort_order'))} | 服务分公式: {_esc(payload.get('score_formula'))}</p>
<details><summary>测速服务配置 (services.json)</summary><pre>{_esc(svc_cfg)}</pre></details>
{''.join(rows) if rows else '<p>暂无节点数据，请先运行探测。</p>'}
</body></html>"""


def cmd_report(open_browser: bool = True) -> int:
    path = save_report()
    if open_browser:
        subprocess.run(["open", str(path)], check=False)
    print(str(path))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "report":
        open_browser = "--no-open" not in args
        try:
            return cmd_report(open_browser=open_browser)
        except Exception as exc:
            log(f"report error: {exc}")
            return 1

    limit = None
    if args and args[0].isdigit():
        limit = int(args[0])
    try:
        run_probe(limit=limit)
        return 0
    except Exception as exc:
        log(f"fatal: {exc}")
        st = load_state()
        st["status"] = "error"
        st["progress"] = str(exc)[:120]
        st["last_run"] = datetime.now(timezone.utc).isoformat()
        save_state(st)
        return 1


if __name__ == "__main__":
    sys.exit(main())
