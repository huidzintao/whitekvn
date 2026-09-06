import base64
import concurrent.futures
import json
import os
import re
import socket
import threading
import time
import urllib.parse
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import requests

MAX_FINAL_KEYS = 50
MAX_LATENCY_MS = 1500
THREADS = 30
UPDATE_INTERVAL_SECONDS = 14400

VALID_PROTOCOLS = (
    "vless://", "trojan://", "hysteria2://", "hy2://",
    "ss://", "vmess://", "tuic://",
)

CURRENT_SUB_B64 = ""
CURRENT_SUB_RAW = ""
TOTAL_ALIVE = 0
LAST_UPDATED = "Идет первый сбор базы..."

def parse_host_port(config: str):
    try:
        config = config.strip()
        if config.startswith("vmess://"):
            b64_part = config[8:].split("#")[0].strip()
            pad = len(b64_part) % 4
            if pad: b64_part += "=" * (4 - pad)
            decoded = base64.b64decode(b64_part).decode("utf-8", errors="ignore")
            data = json.loads(decoded)
            host = data.get("add")
            port = data.get("port")
            if host and port: return str(host), int(port)
            return None
        parsed = urllib.parse.urlparse(config)
        if parsed.hostname and parsed.port: return str(parsed.hostname), int(parsed.port)
        match = re.search(r"@([^:/?#]+):(\d+)", config)
        if match: return match.group(1), int(match.group(2))
    except: pass
    return None

def test_server(config: str):
    host_port = parse_host_port(config)
    if not host_port: return None
    host, port = host_port
    try:
        start_time = time.perf_counter()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(MAX_LATENCY_MS / 1000.0)
        sock.connect((host, port))
        sock.close()
        return config, (time.perf_counter() - start_time) * 1000.0
    except: return None

def fetch_sources():
    if not os.path.exists("sources.txt"): return []
    try:
        with open("sources.txt", "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except: return []
    configs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200: continue
            text = response.text.strip()
            if not text: continue
            if any(proto in text for proto in VALID_PROTOCOLS):
                decoded_text = text
            else:
                cleaned = "".join(text.split())
                pad = len(cleaned) % 4
                if pad: cleaned += "=" * (4 - pad)
                try: decoded_text = base64.b64decode(cleaned).decode("utf-8", errors="ignore")
                except: continue
            for line in decoded_text.splitlines():
                line = line.strip()
                if any(line.startswith(proto) for proto in VALID_PROTOCOLS):
                    configs.append(line)
        except: continue
    seen = set()
    unique = []
    for cfg in configs:
        base = cfg.split("#")[0].strip()
        if base and base not in seen:
            seen.add(base)
            unique.append(cfg)
    return unique

def update_worker():
    global CURRENT_SUB_B64, CURRENT_SUB_RAW, TOTAL_ALIVE, LAST_UPDATED
    while True:
        try:
            raw_keys = fetch_sources()
            alive = []
            if raw_keys:
                with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
                    for res in executor.map(test_server, raw_keys):
                        if res: alive.append(res)
                alive.sort(key=lambda x: x[1])
                top = [x[0] for x in alive[:MAX_FINAL_KEYS]]
                CURRENT_SUB_RAW = "\n".join(top)
                CURRENT_SUB_B64 = base64.b64encode(CURRENT_SUB_RAW.encode("utf-8")).decode("utf-8")
                TOTAL_ALIVE = len(top)
                LAST_UPDATED = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                print(f"Сбор завершен. Живых ключей: {TOTAL_ALIVE}", flush=True)
        except Exception as e:
            print(f"Ошибка сбора: {e}", flush=True)
        time.sleep(UPDATE_INTERVAL_SECONDS)

threading.Thread(target=update_worker, daemon=True).start()

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def index():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>VPN Collector</title>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }}
            h1 {{ color: #2c3e50; }}
            .status {{ background: #d4edda; padding: 15px; border-radius: 5px; margin: 20px 0; }}
            .links {{ background: #f8f9fa; padding: 15px; border-radius: 5px; }}
            code {{ background: #e9ecef; padding: 2px 6px; border-radius: 3px; }}
        </style>
    </head>
    <body>
        <h1>⚡ VPN Subscription Collector</h1>
        <div class="status">
            <strong>Статус:</strong> 🟢 Онлайн<br>
            <strong>Живых ключей:</strong> {TOTAL_ALIVE}<br>
            <strong>Последнее обновление:</strong> {LAST_UPDATED}
        </div>
        <div class="links">
            <h3>Ссылки для VPN-клиента:</h3>
            <p><strong>Base64 подписка:</strong> <code>/sub</code></p>
            <p><strong>Обычный текст:</strong> <code>/sub/raw</code></p>
            <p><strong>Health check:</strong> <code>/health</code></p>
        </div>
    </body>
    </html>
    """

@app.get("/sub")
def get_sub():
    return Response(content=CURRENT_SUB_B64, media_type="text/plain; charset=utf-8")

@app.get("/sub/raw")
def get_sub_raw():
    return Response(content=CURRENT_SUB_RAW, media_type="text/plain; charset=utf-8")

@app.get("/health")
def health():
    return {"status": "ok", "total_alive": TOTAL_ALIVE, "last_updated": LAST_UPDATED}
