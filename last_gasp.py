"""ExecStopPost hook: post a single 'stopped' logger event, best-effort.

Runs as its own short-lived process when the service stops (graceful, or crash
followed by systemd stopping the unit). If power/network are gone it simply
fails silently and the server's dead-man's switch reports the outage instead.
Never raises — always exits 0 so it can't block systemd shutdown.
"""
import sys
import time
import uuid

try:
    import requests
    from config import config
    from api_client import JWTEncoder, APIClient

    api = APIClient()
    api.fetch_all_secret_keys()
    secret = api.secret_key_1
    if secret:
        payload = {"uid": config.server.uid_1, "events": [{
            "event_uid": uuid.uuid4().hex, "type": "stopped",
            "ts": int(time.time()), "severity": "info",
            "detail": "service stop",
        }]}
        token = JWTEncoder.create_jwt(payload, secret)
        requests.post(config.server.logger_events_url, json={"token": token}, timeout=5)
except Exception:
    pass

sys.exit(0)
