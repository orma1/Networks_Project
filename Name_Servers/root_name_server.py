import argparse
from pathlib import Path
from dnslib import DNSRecord, QTYPE, RCODE
from abstract_name_server import AbstractNameServer
class LocalRootServer(AbstractNameServer):
    def __init__(self, config_filename="root_config.yaml", dnssec_enabled=False):
        super().__init__("Local Root Server",'127.0.0.3',config_filename,dnssec_enabled)

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            
            reply = request.reply()
            reply.header.ra = 0 
            reply.header.aa = 1 

            tld = self.extract_tld(qname)
            
            if tld in self.zone_records and getattr(QTYPE, 'NS') in self.zone_records[tld]:
                print(f"[*] Delegating {qname} to .{tld} nameservers.")
                
                # 1. Add NS records to Authority Section
                for ns_rr in self.zone_records[tld][getattr(QTYPE, 'NS')]:
                    reply.add_auth(ns_rr)
                    
                    # 2. Find the Glue A record for this NS and add to Additional Section
                    target_ns = str(ns_rr.rdata)
                    if target_ns in self.zone_records and getattr(QTYPE, 'A') in self.zone_records[target_ns]:
                        for a_rr in self.zone_records[target_ns][getattr(QTYPE, 'A')]:
                            reply.add_ar(a_rr)
                
                if self.dnssec_enabled and getattr(QTYPE, 'TXT') in self.zone_records[tld]:
                        for txt_rr in self.zone_records[tld][getattr(QTYPE, 'TXT')]:
                            txt_data = str(txt_rr.rdata).strip('"')
                            if txt_data.startswith("DS|") or txt_data.startswith("RRSIG|DS|"):
                                reply.add_auth(txt_rr)
                                print(f"    [+] DNSSEC: Attached DS and RRSIG for {tld}")
            else:
                print(f"[*] NXDOMAIN: Unknown TLD '{tld}' for query {qname}")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')

            sock.sendto(reply.pack(), addr)
        except Exception as e:
            print(f"[ERROR] Handling query: {e}")

    def extract_tld(self, qname: str) -> str:
        parts = qname.strip(".").split(".")
        if len(parts) > 0:
            return parts[-1] + "."
        return ""

    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--dnssec', action='store_true')
    args = parser.parse_args()
    root = LocalRootServer(dnssec_enabled=args.dnssec)
    root.start()