import argparse
from pathlib import Path
from dnslib import DNSRecord, QTYPE, RCODE
from abstract_name_server import AbstractNameServer

class LocalTLDServer(AbstractNameServer):
    def __init__(self,config_filename="tld_config.yaml", dnssec_enabled=False ):
        super().__init__("Local TLD Server (.homelab)",'127.0.0.11',config_filename,dnssec_enabled)
    

    def extract_domain(self, qname: str) -> str:
        """ 
        Extracts the domain and TLD from a query. 
        E.g., 'www.test.homelab.' -> 'test.homelab.' 
        """
        parts = qname.strip(".").split(".")
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}."
        return qname

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            
            reply = request.reply()
            reply.header.ra = 0 
            reply.header.aa = 0 # TLD is not the final authority for A records
            
            domain = self.extract_domain(qname)
            
            if domain in self.zone_records and getattr(QTYPE, 'NS') in self.zone_records[domain]:
                print(f"[*] Delegating {qname} to {domain} nameservers.")
                
                # 1. Add NS records to Authority Section
                for ns_rr in self.zone_records[domain][getattr(QTYPE, 'NS')]:
                    reply.add_auth(ns_rr)
                    
                    # 2. Find the Glue A record
                    target_ns = str(ns_rr.rdata)
                    if target_ns in self.zone_records and getattr(QTYPE, 'A') in self.zone_records[target_ns]:
                        for a_rr in self.zone_records[target_ns][getattr(QTYPE, 'A')]:
                            reply.add_ar(a_rr)

                    if self.dnssec_enabled and getattr(QTYPE, 'TXT') in self.zone_records[domain]:
                        for txt_rr in self.zone_records[domain][getattr(QTYPE, 'TXT')]:
                            txt_data = str(txt_rr.rdata).strip('"')
                            if txt_data.startswith("DS|") or txt_data.startswith("RRSIG|DS|"):
                                reply.add_auth(txt_rr)
                                print(f"    [+] DNSSEC: Attached DS and RRSIG for {domain}")
            else:
                print(f"[*] NXDOMAIN: Domain '{domain}' not registered in this TLD.")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')

            sock.sendto(reply.pack(), addr)
        except Exception as e:
            print(f"[ERROR] Handling query: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--dnssec', action='store_true')
    args = parser.parse_args()
    tld = LocalTLDServer(dnssec_enabled=args)
    tld.start()