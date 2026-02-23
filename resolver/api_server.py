import threading
import uvicorn
from fastapi import FastAPI

# We use a global variable to hand the FastAPI app a reference to your Resolver
resolver_ref = None
app = FastAPI(title="DNS Resolver API")

@app.get("/")
def read_root():
    return {"status": "online", "message": "DNS Resolver API is running."}

@app.get("/cache/dns")
def get_dns_cache():
    """Returns the current standard DNS A/CNAME record cache."""
    if not resolver_ref:
        return {"error": "Resolver not linked."}
    
    # Format your custom cache dictionary for JSON output
    formatted_cache = {}
    for key, item in resolver_ref.cache.cache.items():
        domain, qtype = key
        formatted_cache[f"{domain} (Type {qtype})"] = {
            "expires_at": item.expires_at,
            "record_count": len(item.response.rr)
        }
    return {"total_entries": len(formatted_cache), "cache": formatted_cache}

@app.get("/cache/dnssec")
def get_dnssec_cache():
    """Returns the cryptographic DNSKEY and DS vault."""
    if not resolver_ref:
        return {"error": "Resolver not linked."}
    
    return {
        "dnskey_vault": list(resolver_ref.dnskey_cache.keys()),
        "ds_vault": resolver_ref.ds_cache
    }

class APIServer:
    def __init__(self, resolver, host="127.0.0.1", port=8000):
        global resolver_ref
        resolver_ref = resolver
        self.host = host
        self.port = port
        self.thread = None

    def start(self):
        print(f"[*] API Server booting on http://{self.host}:{self.port}")
        # Run Uvicorn in a background thread so it doesn't block your DNS listener
        self.thread = threading.Thread(
            target=uvicorn.run, 
            args=(app,), 
            kwargs={"host": self.host, "port": self.port, "log_level": "critical"}
        )
        self.thread.daemon = True
        self.thread.start()
        
    def stop(self):
        print("[*] API Server shutting down...")
        # Since it's a daemon thread, it will die cleanly when main.py exits