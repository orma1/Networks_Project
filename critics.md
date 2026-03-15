# Documentation Critique: NetworkingFinalDocs.pdf

This critique evaluates the submitted documentation against the assignment requirements (`Assigment.pdf`) and the actual codebase. Findings are organized by section. Each finding states what is wrong, why it matters, and what was expected.

---

## 1. Global / Structural Issues

### 1.1 No Prompts / Sources Section - TODO
**What is wrong:** The assignment explicitly requires including prompts and sources used ("פרומפטים / מקורות"). The documentation contains no such section anywhere in 69 pages.
**Why it matters:** This is a mandatory submission requirement (item 7a in the assignment). Missing it is a direct deduction.
**Expected:** A dedicated appendix listing all RFCs referenced, external sources, and any AI prompts used (the assignment permits AI for theoretical help but requires disclosure).

### 1.2 Wireshark / PCAP Section is Empty - TODO
**What is wrong:** Page 67 is titled "wireshark:" and contains nothing else. The assignment requires PCAP recordings of all relevant traffic and explicitly states: "you are required to record all relevant traffic even if not explicitly stated."
**Why it matters:** PCAP recordings are a mandatory deliverable (item 7b). An empty page signals they were not included.
**Expected:** Annotated screenshots or descriptions of at least: DHCP DORA exchange, DNS iterative resolution chain, TCP handshake + streaming session, RUDP session with visible sequence numbers, DNSSEC validation packets.

### 1.3 State Diagrams Are Missing - TODO
**What is wrong:** The assignment explicitly requires drawing state diagrams ("לצייר דיאגרמת מצבים בהם המערכת עובדת"). There are no state diagrams in any section of the document. What exists are sequence/flow diagrams, which are a different thing.
**Why it matters:** A state diagram shows the system states and transitions (e.g., DHCP: INIT → SELECTING → REQUESTING → BOUND → RENEWING → REBINDING). This is a distinct and required diagram type in networking courses.
**Expected:** At minimum: a DHCP client state machine, a RUDP session state machine (IDLE → CONNECTING → STREAMING → CLOSING), and a DNS resolver state machine.

### 1.4 Packet Loss Handling Not Explained at Document Level - OK - only RUDP\TCP have packet loss handling and ut us documented
**What is wrong:** The assignment requires "להסביר כיצד המערכת מתגברת על איבוד חבילות" (explain how the system handles packet loss). This is partially addressed in the RUDP section but never addressed for DNS or DHCP.
**Why it matters:** DNS over UDP has no built-in retransmission. The resolver's timeout/retry logic is a real implementation concern. DHCP also uses UDP. Neither is addressed.
**Expected:** Per-component explanation of how packet loss is detected and handled, including timeout values and retry counts.

### 1.5 Latency Handling Not Explained - TODO.
**What is wrong:** The assignment requires "להסביר כיצד המערכת מתגברת על בעיות latency" (explain how the system handles latency). This is not addressed in any section.
**Why it matters:** This is a direct assignment requirement. Latency affects timeout tuning in DHCP, DNS cache TTL choices, RUDP timer settings, and DASH quality selection thresholds.
**Expected:** Discussion of timeout values chosen, cache TTLs as a latency mitigation tool, and DASH's role in adaptive quality under variable latency.



### 1.7 REST API (Port 8000) Is Entirely Undocumented - TODO
**What is wrong:** The project includes a FastAPI REST API server (`api_server.py`) running on `127.0.0.1:8000` that allows dynamic DNS record updates. The proxy uses this API at startup to register its DHCP-assigned IP with the DNS zone. This component does not appear anywhere in the documentation.
**Why it matters:** This is a significant architectural component that bridges DHCP → DNS → Proxy. Omitting it leaves the documentation incomplete and makes the system's startup flow incomprehensible to a reader.
**Expected:** Section documenting the API endpoints, their purpose, and how the proxy-DHCP-DNS registration chain works.

### 1.8 Electron/React GUI Client Not Documented - TODO
**What is wrong:** The project contains a full Electron/React/TypeScript GUI client (`DNS/GUI-Client/`) for managing DNS zones. The documentation mentions it in passing ("created a UI in Electron") but provides no architectural description, no explanation of its role, and no instructions beyond "run npm run dev."
**Why it matters:** This is a significant deliverable that took development effort and is part of the system architecture.
**Expected:** Brief architecture description of the GUI: what zones it can edit, how it communicates with the API server, and what operations it supports.

---

## 2. DNS Section Critique (Pages 2–22)

### 2.2 Cache Architecture Not Documented - DONE
**What is wrong:** The resolver implements a full LRU DNS cache (`dns_cache.py`) with TTL-based expiration, automatic background persistence (pickle/binary file), configurable capacity, and dual-lock thread safety. None of this is described.
**Why it matters:** The cache is central to resolver performance and is one of the more technically interesting components (LRU eviction, TTL tracking, disk persistence). Omitting it misrepresents the implementation's depth.
**Expected:** Description of the caching strategy, TTL handling, eviction policy, and persistence mechanism.

### 2.3 Request Coalescing Not Documented - DONE
**What is wrong:** The resolver implements deduplication of in-flight queries: if the same query arrives while resolution is already in progress, the second requester waits on an event rather than launching a parallel upstream query. This is a non-trivial concurrency design.
**Why it matters:** This is exactly the kind of implementation complexity that earns points under "originality and investment" (item 6 in the assignment).
**Expected:** At minimum a paragraph describing the in-flight query deduplication mechanism.

### 2.4 Hot-Reload of Zone Files Not Documented - DONE
**What is wrong:** The `AbstractNameServer` runs a background thread that polls zone directories every 5 seconds and reloads zone files when they change. This enables live DNS record updates without restarting servers.
**Why it matters:** This is a significant operational feature. It enables the proxy registration flow (API updates zone file → auth server hot-reloads within 5s → resolver serves new IP). Without documenting this, the registration flow makes no sense.
**Expected:** Description of the hot-reload mechanism and its role in the dynamic IP registration flow.


### 2.6 DNSSEC Chain of Trust Explanation is Incomplete - DONE
**What is wrong:** Pages 21–22 explain DNSSEC at a conceptual level but omit: (a) how the resolver actually validates the chain (comparing computed SHA-256 hash of child KSK to parent DS record), (b) what happens when validation fails (response is dropped or AD bit cleared), and (c) that validation is opt-in per client request (client must set the DO bit).
**Why it matters:** At university level, a security feature should be explained precisely, including the failure modes.
**Expected:** Algorithmic description of validation: DS record retrieval → KSK hash computation → comparison → AD bit behavior.

---

## 3. DHCP Section Critique (Pages 23–40)

### 3.1 Port Justification is Academically Weak - TODO
**What is wrong:** Page 31 justifies port 6700 by saying it "looks similar to 67" and is from the free range. The actual reason standard DHCP port 67 cannot be used is that binding to privileged ports (<1024) on most operating systems requires administrator/root privileges, which the project avoids.
**Why it matters:** Giving an incorrect or superficial justification for a design decision at university level is academically problematic. A reviewer will question whether the team understands why they made this choice.
**Expected:** "Port 67 requires elevated OS privileges to bind. Port 6700 was chosen as an unprivileged alternative that is recognizably derived from the standard port."

### 3.2 No Proper Architecture Diagram for DHCP - TODO
**What is wrong:** The DHCP section has a text-based ASCII flow diagram (page 28) but no proper architectural diagram showing the components (DHCP Server, DHCP Client/VirtualNetworkInterface, Lease file, Config file) and their relationships.
**Why it matters:** The assignment requires architecture diagrams for each stage ("פירוט הארכיטקטורה של כל שלב").
**Expected:** A component diagram showing: DHCP Server ↔ leases.json, DHCP Server ↔ dhcp_config.yaml, VirtualNetworkInterface ↔ DHCP Server (UDP port 6700), ProxyNode/OriginServer ↔ VirtualNetworkInterface.

### 3.3 DHCP-to-DNS Integration Not Documented - DONE
**What is wrong:** After obtaining an IP from DHCP, the proxy calls `register_with_dns()` to update the DNS A record, then waits for propagation. This DHCP→DNS→Proxy chain is the core of the system's dynamic addressing but is never described as an integrated flow.
**Why it matters:** This integration is what makes the whole system work together. A documentation that treats DHCP, DNS, and the proxy as isolated components fails to capture the system architecture.
**Expected:** A system-level sequence diagram showing: Proxy starts → DHCP DORA → get IP → call DNS API → zone file updated → auth server hot-reloads → resolver cache expires → browser resolves correct IP.

### 3.4 Broadcast Simulation Deviation Not Disclosed - DONE
**What is wrong:** Real DHCP DISCOVER is sent to broadcast address 255.255.255.255. The implementation uses directed UDP to a known server address because all components run on loopback. This deviation from RFC behavior is not disclosed.
**Why it matters:** The document extensively describes RFC-compliant DHCP behavior but doesn't clarify where the implementation diverges. This is misleading.
**Expected:** A clear note: "Because the simulation runs entirely on loopback addresses, DHCP DISCOVER is sent as directed UDP to 127.0.0.1:6700 rather than broadcast. In a real deployment, broadcast would be required."

### ~~3.5 DHCP Renew Logic Has a Design Issue Not Acknowledged~~ *(Fixed)* - TODO - check if all works
**Resolution:** Client IDs were changed from `"{name}-{PID}-{random}"` to just `client_name`. The sticky lease lookup now survives service restarts because the client ID is stable across runs.

---

## 4. Application Server Critique (Pages 41–67)

### 4.1 Fast Recovery Status is Contradictory - TODO
**What is wrong:** 6Page 51 (line 108 in extracted text) explicitly asks "Fast_recovery - האם כדאי לממש?" (Fast Recovery — should we implement it?). This is a planning note left in the submitted document. In the actual code (`congestion_controller.py`), `FAST_RECOVERY` is a defined enum state and the `on_duplicate_ack` method transitions to `CONGESTION_AVOIDANCE` (which subsumes fast recovery behavior).
**Why it matters:** Submitting a document with open implementation questions is unprofessional and suggests incomplete review before submission. It also contradicts the code, creating confusion about what was actually implemented.
**Expected:** Remove the planning note. State clearly: "We implemented three congestion states: SLOW_START, CONGESTION_AVOIDANCE, and FAST_RECOVERY," and describe each transition.

### 4.2 TCP Section Has an Incomplete Sentence - DONE
**What is wrong:** Page 50 ends with "בדומה קיי" — an incomplete sentence fragment ("similar to..."). The explanation of the TCP `handle` function is cut off.
**Why it matters:** This is a clear editing failure. A 69-page document submitted for academic grading should not have incomplete sentences.
**Expected:** Complete explanation of how the TCP handler works: parsing the REQ message, reading the file, streaming in chunks, and handling disconnection.

### 4.3 Proxy Architecture is Not Documented - TODO
**What is wrong:** `proxy_app.py` is a full FastAPI application that: (1) registers its IP with DNS, (2) resolves the origin server via DNS, (3) serves a web UI for video selection and protocol switching, (4) adapts quality using DASH logic, (5) proxies video via either TCP or RUDP, and (6) simulates packet loss. This component has no dedicated documentation section.
**Why it matters:** The proxy is arguably the most complex component in the Application tier. Omitting it means the application server architecture is documented incompletely.
**Expected:** Section covering: proxy's role, FastAPI routes (`/`, `/stream/{filename}`, `/switch_protocol`, `/set_loss`), DNS resolution at startup, quality adaptation logic, and how it interfaces with the origin server.

### 4.4 DASH Implementation Diverges from Standard Without Adequate Justification - TODO
**What is wrong:** Real DASH (MPEG-DASH) uses an MPD manifest file, HTTP byte-range requests, and continuous per-segment bandwidth measurement. The implementation does none of these: quality is switched manually (for RUDP) or via a single bandwidth sample at stream open (for TCP). Page 66 acknowledges this but the justification ("simplified implementation") is insufficient for university level.
**Why it matters:** The assignment asks for a DASH video server (option 1). If the implementation substantially deviates from the DASH specification, the deviation should be formally acknowledged and justified.
**Expected:** "Our implementation is DASH-inspired rather than DASH-compliant. We omit the MPD manifest, chunk segmentation, and HTTP-based delivery because [specific reasons]. The adaptive quality selection logic implements the core DASH ABR concept." Then explain what was and was not implemented.

### 4.5 RUDP Fixed Window is Not Justified - TODO
**What is wrong:** Page 48 states "we implemented a dynamic window but with a fixed size (64)." A dynamic window with a fixed size is a contradiction in terms. The actual code starts with `INIT_CWND=4.0` and grows via congestion control, with a separate receiver window (rwnd). Calling it "fixed size 64" is inaccurate.
**Why it matters:** Congestion and flow control are core grading criteria (listed explicitly in the assignment). An inaccurate description of these mechanisms will cause a grader to doubt whether they were implemented correctly.
**Expected:** Accurate description: "The congestion window starts at INIT_CWND=4 segments and grows per the congestion controller. The receiver advertises a window of 1MB. The effective window is min(cwnd, rwnd)."

### 4.6 Admission of Using AI for UI Code - TODO
**What is wrong:** Page 64 states "השתמשנו בgemini לעזרה בUI" (we used Gemini to help with the UI). The assignment explicitly states that using AI models to write code is treated as plagiarism: "שימוש ב-chat gpt או בכל מודל/סוכן שכותב את הקוד במקומכם הינו העתקה לכל דבר."
**Why it matters:** If Gemini wrote any of the UI code, this is a direct violation of academic integrity rules as defined in the assignment. Even if it only provided "help," the admission should have been accompanied by the exact prompt used (which the assignment requires for any AI assistance).
**Expected:** Either: (a) clarify that Gemini was used only for conceptual/design advice, not code, and include the prompt used; or (b) acknowledge the violation and remove the UI code from the submission.

### 4.7 Session Manager's Role is Under-Explained - DONE
**What is wrong:** `session_manager.py` is described briefly (pages 60–62) but its relationship to the RUDP protocol is not explained clearly. Specifically: why is session management needed if each stream is a new request? The keep-alive mechanism that justifies session tracking is mentioned on page 48 but never connected to the session manager implementation.
**Why it matters:** The session manager solves a real problem (connectionless UDP requires explicit session tracking), and explaining this connection demonstrates understanding.
**Expected:** Explicit statement: "Because UDP is connectionless, the server has no way to know when a client disconnects. The session manager tracks active sessions and closes stale ones using a keep-alive timeout of X seconds."

## 5. Theoretical Questions Critique (Pages 68–69)

### 5.1 Question 4: NAT Explanation is Missing - TODO
**What is wrong:** Question 4 asks to fill the packet table AND "explain how messages would change if there was NAT between the user and servers, and whether you would use QUIC." The packet table is present. The NAT and QUIC discussion is entirely absent.
**Why it matters:** This is half of question 4 unanswered. Direct deduction.
**Expected:** Explanation that with NAT: (1) IP Src would be translated at the NAT boundary, (2) DNS responses would need to reference the NATted address, (3) DHCP would need to operate on the private side only. For QUIC: it uses Connection IDs rather than IP:port pairs, making it more NAT-friendly as connection IDs survive IP changes.

### 5.2 Question 4: Source Port Missing in DNS Row - TODO
**What is wrong:** The DNS row in the packet table has no source port. DNS queries from a client use an ephemeral (random high-numbered) source port. Leaving it blank is inaccurate.
**Expected:** "Port Src: ephemeral (random, typically 49152–65535), Port Des: 53"

### 5.3 Question 4: Same MAC for Source and Destination - TODO
**What is wrong:** The table shows identical MAC addresses for both Src and Des for all rows. While technically accurate for loopback traffic (all packets stay on one NIC), this should be explicitly acknowledged as a loopback-only artifact, not a general property.
**Expected:** Note: "Because all communication is over loopback (127.x.x.x), all packets use the same physical interface and MAC address. In a real network, Src MAC and Des MAC would differ across subnets."

### 5.4 Question 2: Cubic/Vegas Missing Key Points - TODO
**What is wrong:** The Cubic vs Vegas answer is correct but shallow. Missing: (a) the fairness problem — Vegas is aggressive Cubic-unfriendly because Cubic fills the pipe faster; Vegas backs off when it detects latency increases, so in a shared network Cubic "steals" bandwidth from Vegas. (b) The mathematical form of Cubic's window function (cubic polynomial in time since last congestion event). (c) Vegas measures expected vs actual throughput rate to detect congestion before loss occurs.
**Why it matters:** For university-level networking, fairness and coexistence behavior are fundamental topics.

### 5.5 Question 3: OSPF Description Slightly Imprecise - TODO
**What is wrong:** The document states "OSPF will choose the fastest path, not necessarily the shortest." More precisely: OSPF uses Dijkstra's shortest-path algorithm on a weighted link-state graph. "Fastest" is misleading — link costs in OSPF are configurable and can represent bandwidth, delay, or administrative preference. The algorithm finds the lowest-cost path, which administrators configure to reflect their priorities.
**Expected:** "OSPF uses Dijkstra's algorithm on a link-state topology to find the minimum-cost path, where link costs are configurable to reflect bandwidth or latency."

---

## 6. Summary — Priority Issues

The following issues are the most likely to result in grade deductions, ranked by severity:

| # | Issue | Section | Severity |
|---|-------|---------|----------|
| 1 | Wireshark / PCAP page is completely empty | Global | Critical — mandatory deliverable |
| 2 | State diagrams absent throughout | Global | Critical — explicitly required |
| 3 | Wrong IP addresses documented (TLD=127.0.0.11, Auth=127.0.0.12) | DNS | High — directly contradicts code |
| 4 | NAT/QUIC answer missing for Question 4 | Questions | High — half the question unanswered |
| 5 | No prompts/sources section | Global | High — mandatory requirement |
| 6 | "Should we implement Fast Recovery?" planning note in submitted doc | App | High — unprofessional, contradicts code |
| 7 | Admission of using Gemini for UI code without prompt disclosure | App | High — potential academic integrity issue |
| 8 | TCP handle function section is an incomplete sentence | App | Medium — editing failure |
| 9 | REST API (port 8000) entirely undocumented | Global | Medium — key architectural component |
| 10 | Packet loss / latency handling not addressed for DNS and DHCP | Global | Medium — explicit assignment requirement |
| 11 | Integration tests not mentioned | Global | Medium — 40% grade weight on testing |
| 12 | DHCP-to-DNS integration flow not documented | DHCP | Medium — core system feature |
| 13 | DASH divergence from standard not formally justified | App | Medium — core feature of assignment option 1 |
| 14 | RUDP window description is inaccurate ("fixed size 64") | App | Medium — misrepresents implementation |
| 14 | Broadcast simulation deviation not disclosed | DHCP | Low — misleading but minor |
