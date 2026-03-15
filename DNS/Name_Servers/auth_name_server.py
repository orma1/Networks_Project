import argparse

from dnslib import DNSRecord, QTYPE, RCODE

from abstract_name_server import AbstractNameServer


class LocalAuthServer(AbstractNameServer):
    def __init__(self, config_filename="auth_config.yaml", dnssec_enabled=False):
        super().__init__(
            "Local Auth Server", "127.0.0.5", config_filename, dnssec_enabled
        )

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            qtype = request.q.qtype

            reply = request.reply()
            reply.header.ra = 0
            reply.header.aa = 1

            # Use our new zone_records (renamed from a_records)
            if qname in self.zone_records:
                node = self.zone_records[qname]

                # CASE 1: They asked for a specific record type (like A or NS)
                if qtype in node:
                    for rr in node[qtype]:
                        reply.add_answer(rr)
                        print(f"[*] ANSWER: Appended {QTYPE[qtype]} record for {qname}")

                    if self.dnssec_enabled and QTYPE.TXT in node:
                        for txt_rr in node[QTYPE.TXT]:
                            txt_data = str(txt_rr.rdata).strip('"')
                            # If the TXT record is an RRSIG for the record type we just answered, attach it!
                            if txt_data.startswith(f"RRSIG|{QTYPE[qtype]}|"):
                                reply.add_answer(txt_rr)
                                print(f"    [+] DNSSEC: Attached RRSIG for {QTYPE[qtype]}")

                # CASE 2: CNAME resolution (They asked for A, but we only have CNAME)
                elif QTYPE.CNAME in node and qtype == QTYPE.A:
                    for cname_rr in node[QTYPE.CNAME]:
                        reply.add_answer(cname_rr)

                        # Attach CNAME Signature
                        if self.dnssec_enabled and QTYPE.TXT in node:
                            for txt_rr in node[QTYPE.TXT]:
                                if str(txt_rr.rdata).strip('"').startswith("RRSIG|CNAME|"):
                                    reply.add_answer(txt_rr)
                                    print(f"    [+] DNSSEC: Attached RRSIG for CNAME")

                        # CNAME Chasing: Do we also have the A record for the target?
                        target_name = str(cname_rr.rdata)
                        if (
                            target_name in self.zone_records
                            and QTYPE.A in self.zone_records[target_name]
                        ):
                            target_node = self.zone_records[target_name]
                            for target_a_rr in target_node[QTYPE.A]:
                                reply.add_answer(target_a_rr)
                                print(f"[*] CNAME CHASE: Appended A record for {target_name}")

                            # Attach Chased A Record Signature
                            if self.dnssec_enabled and QTYPE.TXT in target_node:
                                for txt_rr in target_node[QTYPE.TXT]:
                                    if str(txt_rr.rdata).strip('"').startswith("RRSIG|A|"):
                                        reply.add_answer(txt_rr)
                                        print(f"    [+] DNSSEC: Attached RRSIG for Chased A record")

                # CASE 3: The name exists, but not the requested type
                else:
                    print(f"[*] NODATA: Name '{qname}' exists, but no {QTYPE[qtype]} records.")
            else:
                print(f"[*] NXDOMAIN: Domain '{qname}' does not exist.")
                reply.header.rcode = RCODE.NXDOMAIN

            # If we didn't add any answers, we must provide the SOA record in the Authority section
            if len(reply.rr) == 0:
                parts = qname.strip(".").split(".")
                # Climb up the domain tree (x.test.homelab. -> test.homelab. -> homelab.)
                for i in range(len(parts)):
                    apex = ".".join(parts[i:]) + "."
                    if apex in self.zone_records and QTYPE.SOA in self.zone_records[apex]:
                        # 1. Add the SOA record
                        for soa_rr in self.zone_records[apex][QTYPE.SOA]:
                            reply.add_auth(soa_rr)
                            print(f"    [*] Attached SOA record for {apex} to Authority section.")

                        # 2. Add the RRSIG for the SOA record (if DNSSEC is enabled)
                        if self.dnssec_enabled and QTYPE.TXT in self.zone_records[apex]:
                            for txt_rr in self.zone_records[apex][QTYPE.TXT]:
                                if str(txt_rr.rdata).strip('"').startswith("RRSIG|SOA|"):
                                    reply.add_auth(txt_rr)
                                    print(f"    [+] DNSSEC: Attached RRSIG for SOA record")
                        break

            sock.sendto(reply.pack(), addr)

        except Exception as e:
            print(f"[ERROR] Handling query: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dnssec", action="store_true")
    args = parser.parse_args()
    auth = LocalAuthServer(dnssec_enabled=args.dnssec)
    auth.start()