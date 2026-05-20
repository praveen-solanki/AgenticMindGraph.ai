# """
# config/settings.py
# ==================
# Single source of truth for every tunable parameter in the pipeline.
# Edit this file to adapt the pipeline to your specific AUTOSAR corpus.
# """

# from __future__ import annotations
# import os
# from pathlib import Path

# # ══════════════════════════════════════════════════════════════════════════════
# # PIPELINE VERSION
# # Bump this string whenever the extraction logic or schema changes.
# # Stored on every node as `pipeline_version` for future ASEI drift detection.
# # ══════════════════════════════════════════════════════════════════════════════

# PIPELINE_VERSION = os.environ.get("PIPELINE_VERSION", "1.1.0")

# # ══════════════════════════════════════════════════════════════════════════════
# # NEO4J
# # ══════════════════════════════════════════════════════════════════════════════

# NEO4J_URI      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687")
# NEO4J_USER     = os.environ.get("NEO4J_USER",     "neo4j")
# NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD",  "autosar123")

# # ══════════════════════════════════════════════════════════════════════════════
# # vLLM / LLM
# # ══════════════════════════════════════════════════════════════════════════════

# VLLM_BASE_URL  = os.environ.get("VLLM_URL", "http://localhost:8011/v1")
# VLLM_API_KEY   = "dummy"                    # vLLM ignores the key
# LLM_MODEL      = "Qwen/Qwen2.5-72B-Instruct-AWQ"
# LLM_TEMPERATURE        = 0                  # deterministic for extraction
# LLM_MAX_TOKENS         = 8192              # enough for entity extraction JSON
# LLM_TIMEOUT            = 600               # seconds per request
# LLM_MAX_CONCURRENT     = 16              # match --max-num-seqs 16

# # ══════════════════════════════════════════════════════════════════════════════
# # EMBEDDINGS
# # ══════════════════════════════════════════════════════════════════════════════

# EMBED_MODEL    = "BAAI/bge-m3"
# EMBED_DIM      = 1024
# EMBED_BATCH_SIZE_GPU = 16
# EMBED_BATCH_SIZE_CPU = 8
# EMBED_NORMALIZE      = True

# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 1 — PDF EXTRACTION
# # ══════════════════════════════════════════════════════════════════════════════

# # Fraction of page height to crop as header / footer
# PDF_HEADER_MARGIN = 0.12   # 12% top  — AUTOSAR docs have large headers
# PDF_FOOTER_MARGIN = 0.12   # 10% bottom — page numbers + "AUTOSAR confidential"

# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 2 — NOISE REMOVAL
# # ══════════════════════════════════════════════════════════════════════════════

# # A line appearing on this fraction of pages or more = running header/footer
# REPEATED_LINE_THRESHOLD   = 0.40

# # If this fraction of lines on a page match TOC pattern = TOC page → drop
# TOC_LINE_RATIO_THRESHOLD  = 0.50

# # If this fraction of lines contain date/version patterns = revision page
# REVISION_LINE_RATIO       = 0.40

# # Pages with fewer than this many chars after cleaning = near-blank → drop
# MIN_PAGE_CHARS            = 150

# # Lines shorter than this starting with Figure/Table/etc = orphaned caption
# CAPTION_MAX_LEN           = 40

# # Cross-document boilerplate: cosine similarity above this = same boilerplate
# BOILERPLATE_SIM_THRESHOLD = 0.95

# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 3 — REQUIREMENT ID HARVESTING
# # ══════════════════════════════════════════════════════════════════════════════

# # Max IDs per page before skipping cross-ref pair generation (index page guard)
# MAX_IDS_PER_PAGE_FOR_XREF = 15

# # Regex patterns for AUTOSAR IDs — add more as needed for your corpus

# REQUIREMENT_ID_PATTERNS = [
#     # --- Existing patterns ---
#     r"\[SWS_[A-Za-z]+_\d{5}\]",        # Software Specification (SWS)
#     r"\[SRS_[A-Za-z]+_\d{5}\]",        # Software Requirements Spec (SRS)
#     r"\[constr_\d{4}\]",                # Constraints
#     r"\[ECUC_[A-Za-z]+_\d{5}\]",       # ECU Configuration parameters
#     r"\[BSW_\d{5}\]",                   # Basic Software requirements
#     r"\[ASWS_[A-Za-z]+_\d{5}\]",       # Application SW requirements

#     # --- Requirements Specification (RS) ---
#     r"\[RS_[A-Za-z]+_\d{5}\]",         # General RS requirements

#     # --- Protocol Specification (PRS) ---
#     r"\[PRS_[A-Za-z]+_\d{5}\]",        # Protocol requirements (SOME/IP, NM, TimeSync, etc.)

#     # --- Acceptance Test Specification (ATS) ---
#     r"\[ATS_[A-Za-z]+_\d{5}\]",        # Acceptance test requirements

#     # --- Explanatory Documents (EXP) ---
#     r"\[EXP_[A-Za-z]+_\d{5}\]",        # Explanatory document refs

#     # --- Safety & Security ---
#     r"\[SafetyReq_[A-Za-z]+_\d{5}\]",  # Functional safety requirements
#     r"\[SecReq_[A-Za-z]+_\d{5}\]",     # Security requirements
#     r"\[IAM_[A-Za-z]+_\d{5}\]",        # Identity and Access Management

#     # --- Diagnostics ---
#     r"\[DiagReq_[A-Za-z]+_\d{5}\]",    # Diagnostic requirements
#     r"\[DEM_[A-Za-z]+_\d{5}\]",        # Diagnostic Event Manager
#     r"\[DCM_[A-Za-z]+_\d{5}\]",        # Diagnostic Communication Manager

#     # --- Communication (SOME/IP, COM, NM) ---
#     r"\[SOMEIP_[A-Za-z]+_\d{5}\]",     # SOME/IP protocol
#     r"\[COM_[A-Za-z]+_\d{5}\]",        # Communication requirements
#     r"\[NM_[A-Za-z]+_\d{5}\]",         # Network Management
#     r"\[E2E_[A-Za-z]+_\d{5}\]",        # End-to-End protection

#     # --- Execution & State Management ---
#     r"\[ExecReq_[A-Za-z]+_\d{5}\]",    # Execution Management
#     r"\[SM_[A-Za-z]+_\d{5}\]",         # State Management
#     r"\[PHM_[A-Za-z]+_\d{5}\]",        # Platform Health Management

#     # --- Cryptography & Security ---
#     r"\[Crypto_[A-Za-z]+_\d{5}\]",     # Cryptography requirements

#     # --- Persistency, Log & Trace ---
#     r"\[PER_[A-Za-z]+_\d{5}\]",        # Persistency
#     r"\[LOG_[A-Za-z]+_\d{5}\]",        # Log and Trace

#     # --- Time Synchronization ---
#     r"\[TS_[A-Za-z]+_\d{5}\]",         # Time Synchronization

#     # --- Update & Config Management ---
#     r"\[UCM_[A-Za-z]+_\d{5}\]",        # Update and Config Management

#     # --- Driver / Hardware Abstraction (SRS_*Driver) ---
#     r"\[ADC_[A-Za-z]+_\d{5}\]",        # ADC Driver
#     r"\[DIO_[A-Za-z]+_\d{5}\]",        # DIO Driver
#     r"\[GPT_[A-Za-z]+_\d{5}\]",        # GPT Driver
#     r"\[ICU_[A-Za-z]+_\d{5}\]",        # ICU Driver
#     r"\[MCU_[A-Za-z]+_\d{5}\]",        # MCU Driver
#     r"\[PWM_[A-Za-z]+_\d{5}\]",        # PWM Driver (common in AUTOSAR)
#     r"\[SPI_[A-Za-z]+_\d{5}\]",        # SPI Driver (common in AUTOSAR)
#     r"\[IOHW_[A-Za-z]+_\d{5}\]",       # IO Hardware Abstraction

#     # --- Flexible / Generic fallback ---
#     r"\[[A-Z][A-Za-z0-9]+_[A-Za-z]+_\d{5}\]",  # Any AUTOSAR-style bracketed ID
# ]
# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 4 — CHUNKING
# # ══════════════════════════════════════════════════════════════════════════════

# CHUNK_MAX_TOKENS      = 756    # Max tokens per chunk
# CHUNK_OVERLAP_TOKENS  = 128     # Overlap when splitting oversized chunks
# CHUNK_MIN_TOKENS      = 40     # Drop chunks smaller than this
# CHUNK_TABLE_MAX_TOKENS= 1200    # Tables may exceed normal max — kept whole
# MIN_UNIQUE_WORD_RATIO = 0.30   # Drop chunks with low lexical diversity

# # Heading levels to split on

# SPLIT_HEADERS = [
#     ("#",     "H1"),
#     ("##",    "H2"),
#     ("###",   "H3"),
#     ("####",  "H4"),
#     ("#####", "H5"),
# ]

# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 5 — ENTITY & RELATION EXTRACTION SCHEMA
# # ══════════════════════════════════════════════════════════════════════════════
# # Customize these to match your specific AUTOSAR document corpus.
# # Read 20-30 pages of your actual docs first, then adjust.

# ALLOWED_NODES = [
#     # --- Your originals (unchanged) ---
#     "Requirement",        # [SWS_X_NNNNN], [SRS_X_NNNNN], [TR_X_NNNNN], [AP_TPS_*] etc.
#     "Document",   # First-class AUTOSAR PDF/document entity
#     "ConfigParameter",    # ECUC parameter definitions, struct fields, enum values
#     "Module",             # AUTOSAR SW module (ComM, NvM, Can, Dcm, CSM, CRYIF ...)
#     "Concept",            # Abstract AUTOSAR concept (PDU, Signal, Frame, Job, Key, Task ...)
#     "StandardRef",        # External standard (ISO 26262, POSIX, MISRA, AUTOSAR_SRS_General)
#     "DocumentRef",        # Another AUTOSAR document referenced from this one
#     "Function",           # Software function or API (ArtiInit(), Csm_MacGenerate(), Rte_Call_* ...)
#     "System",             # System or sub-system (Adaptive Platform, Classic Platform, ECU)
#     "Organization",       # Company or standards body (AUTOSAR, OEM, Tier-1)

#     # --- Added: design & model elements ---
#     "FunctionalCluster",  # AP-specific named cluster (Execution Management, Communication Management ...)
#     "DataType",           # Type definition: struct, enum, typedef (ArtiVersionInfoType, CallingContext ...)
#     "Class",              # Meta-model class defined in a TPS doc (ApmcFunctionalClusterDef, Process ...)
#     "Category",           # AUTOSAR category value (STANDARDIZED_CLUSTER_DEFINITION, VENDOR_SPECIFIC ...)

#     # --- Added: document structure ---
#     "SpecificationItem",  # Numbered spec item block ⌈...⌋ that may wrap one or more requirements
#     "ChangeRecord",       # Entry in Document Change History (release, changed-by, description)
#     "Constraint",         # Modelling constraint ([constr_NNNN], binding-time rule, M1/M2 check)

#     "TestSpecification",   # ATS / test-spec documents
#     "TestCase",            # individual test cases inside ATS / test specs
# ]

# ALLOWED_RELATIONSHIPS = [
#     # --- Your originals (unchanged) ---
#     "REFERENCES",         # requirement/concept references another item
#     "IMPLEMENTS",         # module/function implements a requirement
#     "DEFINED_BY",         # concept/requirement defined by a standard or document
#     "DEPENDS_ON",         # module/requirement depends on another module/requirement
#     "ALLOCATED_TO",       # requirement allocated to a module or functional cluster
#     "CONFIGURES",         # config parameter configures a module or behavior
#     "SPECIALIZES",        # concept specializes a more abstract concept (subtype)
#     "DERIVED_FROM",       # requirement derived from a parent requirement (vertical trace)
#     "CONTRADICTS",        # LLM-inferred semantic conflict between two items
#     "TRACES_TO",          # SRS → SWS forward tracing
#     "HAS_PARAMETER",      # module/function has a config parameter or typed parameter
#     "TESTED_BY",          # requirement tested by a test case or test specification
#     "REFINES",            # more specific version of a sibling requirement or concept

#     # --- Added: implementation & call chain ---
#     "CALLS",              # function directly calls another function (precise API call chain,
#                           # e.g. Csm_MacGenerate → CryIf_ProcessJob → Crypto_ProcessJob)
#                           # distinct from DEPENDS_ON which is module-level architectural coupling

#     # --- Added: ownership & composition ---
#     "OWNED_BY",           # document or module is owned by an organization or functional cluster
#     "PART_OF",            # module is part of a system; cluster is part of AP/CP; field is part of struct
#     "CONTAINS",           # document contains a SpecificationItem; module contains a sub-module
#     "HAS_CONFIG",         # module has a configuration container or parameter set (coarser than HAS_PARAMETER)

#     # --- Added: taxonomy ---
#     "INSTANCE_OF",        # a modelled element is an instance of a meta-model class
#     "HAS_CATEGORY",       # class or concept has an allowed category value

#     # --- Added: document lifecycle ---
#     "CHANGED_IN",         # spec item or document was added/changed/deleted in a given release

#     # --- Added: definition location ---
#     "DEFINED_IN",         # concept, class, or data type is formally defined in a specific document
#                           # complements DEFINED_BY: DEFINED_BY points to the authority (standard/org),
#                           # DEFINED_IN points to the exact document containing the formal definition
# ]
# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 6 — ENTITY RESOLUTION
# # ══════════════════════════════════════════════════════════════════════════════

# ENTITY_RESOLUTION_THRESHOLD       = 0.92  # Cosine similarity for same-entity clustering (certain)
# ENTITY_RESOLUTION_UNCERTAIN_LOW   = 0.75  # Lower bound of uncertain zone — sent to LLM
# ENTITY_RESOLUTION_UNCERTAIN_HIGH  = 0.92  # Upper bound of uncertain zone — sent to LLM

# # Manual canonical name overrides — common AUTOSAR abbreviation variants.
# # # Keys are lowercased for matching. Extend with your corpus-specific variants.

# CANONICAL_NAME_OVERRIDES: dict[str, str] = {
#     # -------------------------------------------------------------------------
#     # Module name variants (original)
#     # -------------------------------------------------------------------------
#     "communication manager":                        "ComM",
#     "comm":                                         "ComM",
#     "can":                                          "Can",
#     "controller area network":                      "Can",
#     "nvm":                                          "NvM",
#     "non-volatile memory":                          "NvM",
#     "nonvolatile memory":                           "NvM",
#     "dcm":                                          "Dcm",
#     "diagnostic communication manager":             "Dcm",
#     "dem":                                          "Dem",
#     "diagnostic event manager":                     "Dem",
#     "rte":                                          "RTE",
#     "runtime environment":                          "RTE",
#     "os":                                           "Os",
#     "operating system":                             "Os",
#     "com":                                          "Com",
#     "bsw":                                          "BSW",
#     "basic software":                               "BSW",
#     "mcal":                                         "MCAL",

#     # -------------------------------------------------------------------------
#     # Standard name variants (original)
#     # -------------------------------------------------------------------------
#     "iso26262":                                     "ISO 26262",
#     "iso 26262:2018":                               "ISO 26262",
#     "iso 26262:2011":                               "ISO 26262",
#     "iec 61508":                                    "IEC 61508",

#     # -------------------------------------------------------------------------
#     # Concept variants (original)
#     # -------------------------------------------------------------------------
#     "pdu":                                          "PDU",
#     "protocol data unit":                           "PDU",
#     "i-pdu":                                        "I-PDU",
#     "ipdu":                                         "I-PDU",
#     "i_pdu":                                        "I-PDU",
#     "sdu":                                          "SDU",
#     "service data unit":                            "SDU",
#     "ecu":                                          "ECU",
#     "electronic control unit":                      "ECU",

#     # -------------------------------------------------------------------------
#     # EXP – Explanation / Guideline documents
#     # -------------------------------------------------------------------------
#     "adaptive platform":                            "Adaptive Platform",
#     "ap":                                           "Adaptive Platform",
#     "adaptive autosar":                             "Adaptive Platform",
#     "adaptive platform machine configuration":      "Adaptive Platform Machine Configuration",
#     "machine configuration":                        "Adaptive Platform Machine Configuration",
#     "ara com":                                      "ara::com API",
#     "ara::com":                                     "ara::com API",
#     "aracom":                                       "ara::com API",
#     "ara com api":                                  "ara::com API",
#     "bsw distribution":                             "BSW Distribution Guide",
#     "basic software distribution":                  "BSW Distribution Guide",
#     "cdd":                                          "CDD",
#     "complex device driver":                        "CDD",
#     "cdd design":                                   "CDD Design and Integration Guideline",
#     "cdd integration":                              "CDD Design and Integration Guideline",
#     "application level error handling":             "Application-Level Error Handling",
#     "error handling":                               "Application-Level Error Handling",
#     "error description":                            "Error Description",
#     "functional safety measures":                   "Functional Safety Measures",
#     "fusa":                                         "Functional Safety",
#     "functional safety":                            "Functional Safety",
#     "safety overview":                              "Safety Overview",
#     "safety use case":                              "Safety Use Case",
#     "interrupt handling":                           "Interrupt Handling Explanation",
#     "isr":                                          "ISR",
#     "interrupt service routine":                    "ISR",
#     "ipsec":                                        "IPsec",
#     "ip security":                                  "IPsec",
#     "ipsec implementation":                         "IPsec Implementation Guidelines",
#     "layered software architecture":                "Layered Software Architecture",
#     "lsa":                                          "Layered Software Architecture",
#     "macro encapsulation":                          "Macro Encapsulation of Interpolation Calls",
#     "interpolation calls":                          "Macro Encapsulation of Interpolation Calls",
#     "mode management":                              "Mode Management Guide",
#     "modemanagement":                               "Mode Management Guide",
#     "nv data handling":                             "NV Data Handling",
#     "nvdata":                                       "NV Data Handling",
#     "non-volatile data handling":                   "NV Data Handling",
#     "parallel processing":                          "Parallel Processing Guidelines",
#     "multicore":                                    "Parallel Processing Guidelines",
#     "multi-core":                                   "Parallel Processing Guidelines",
#     "platform design":                              "Platform Design",
#     "sensor interfaces":                            "Sensor Interfaces",
#     "someip":                                       "SOME/IP",
#     "some/ip":                                      "SOME/IP",
#     "some ip":                                      "SOME/IP",
#     "scalable service-oriented middleware over ip":  "SOME/IP",
#     "ai body and comfort":                          "AI Body and Comfort",
#     "ai chassis":                                   "AI Chassis",
#     "ai hmi multimedia and telematics":             "AI HMI Multimedia and Telematics",
#     "ai occupant and pedestrian safety":            "AI Occupant and Pedestrian Safety",
#     "ai powertrain":                                "AI Powertrain",
#     "ai user guide":                                "AI User Guide",
#     "crypto services":                              "Crypto Services",
#     "cryptographic services":                       "Crypto Services",
#     "utilization of crypto services":               "Utilization of Crypto Services",
#     "vfb":                                          "VFB",
#     "virtual functional bus":                       "VFB",

#     # -------------------------------------------------------------------------
#     # PRS – Protocol Specification documents
#     # -------------------------------------------------------------------------
#     "e2e protocol":                                 "E2E Protocol",
#     "end to end protocol":                          "E2E Protocol",
#     "end-to-end protocol":                          "E2E Protocol",
#     "e2e":                                          "E2E",
#     "end-to-end":                                   "E2E",
#     "end to end":                                   "E2E",
#     "log and trace protocol":                       "Log and Trace Protocol",
#     "lat protocol":                                 "Log and Trace Protocol",
#     "nm protocol":                                  "NM Protocol",
#     "network management protocol":                  "NM Protocol",
#     "autosar nm":                                   "NM Protocol",
#     "someip protocol":                              "SOME/IP Protocol",
#     "some/ip protocol":                             "SOME/IP Protocol",
#     "someip sd":                                    "SOME/IP Service Discovery Protocol",
#     "some/ip sd":                                   "SOME/IP Service Discovery Protocol",
#     "someip service discovery":                     "SOME/IP Service Discovery Protocol",
#     "some/ip service discovery":                    "SOME/IP Service Discovery Protocol",
#     "testability protocol":                         "Testability Protocol and Service Primitives",
#     "tap":                                          "Testability Protocol and Service Primitives",
#     "time sync protocol":                           "Time Sync Protocol",
#     "time synchronization protocol":                "Time Sync Protocol",
#     "timesync protocol":                            "Time Sync Protocol",

#     # -------------------------------------------------------------------------
#     # RS – Requirement Specification documents
#     # -------------------------------------------------------------------------
#     "bsw module description template":              "BSW Module Description Template",
#     "bswmdt":                                       "BSW Module Description Template",
#     "communication management":                     "Communication Management",
#     "cpp14 guidelines":                             "C++14 Guidelines",
#     "cpp14":                                        "C++14",
#     "c++14":                                        "C++14",
#     "c++ 14":                                       "C++14",
#     "c++14 guidelines":                             "C++14 Guidelines",
#     "cryptography":                                 "Cryptography",
#     "crypto":                                       "Cryptography",
#     "diagnostic extract template":                  "Diagnostic Extract Template",
#     "det":                                          "DET",
#     "ecu configuration":                            "ECU Configuration",
#     "ecuc":                                         "ECU Configuration",
#     "ecu resource template":                        "ECU Resource Template",
#     "ecurt":                                        "ECU Resource Template",
#     "execution management":                         "Execution Management",
#     "em":                                           "Execution Management",
#     "execm":                                        "Execution Management",
#     "feature model exchange format":                "Feature Model Exchange Format",
#     "fmef":                                         "Feature Model Exchange Format",
#     "autosar features":                             "AUTOSAR Features",
#     "foundation debug trace profile":               "Foundation Debug Trace Profile",
#     "fdtp":                                         "Foundation Debug Trace Profile",
#     "autosar general":                              "General Requirements",
#     "rs general":                                   "General Requirements",
#     "health monitoring":                            "Health Monitoring",
#     "phm":                                          "Platform Health Management",
#     "platform health management":                   "Platform Health Management",
#     "identity and access management":               "Identity and Access Management",
#     "iam":                                          "Identity and Access Management",
#     "interaction with behavioral models":           "Interaction with Behavioral Models",
#     "behavioral models":                            "Behavioral Models",
#     "interoperability of autosar tools":            "Interoperability of AUTOSAR Tools",
#     "tool interoperability":                        "Interoperability of AUTOSAR Tools",
#     "log and trace":                                "Log and Trace",
#     "lat":                                          "Log and Trace",
#     "logandtrace":                                  "Log and Trace",
#     "rs main":                                      "RS Main",
#     "autosar rs main":                              "RS Main",
#     "manifest specification":                       "Manifest Specification",
#     "manifest spec":                                "Manifest Specification",
#     "arxml manifest":                               "Manifest Specification",
#     "methodology and templates general":            "Methodology and Templates General",
#     "methodology general":                          "Methodology and Templates General",
#     "autosar methodology":                          "Methodology",
#     "rs methodology":                               "Methodology",
#     "network management":                           "Network Management",
#     "nm":                                           "NM",
#     "operating system interface":                   "Operating System Interface",
#     "os interface":                                 "OS Interface",
#     "persistency":                                  "Persistency",
#     "per":                                          "Persistency",
#     "project objectives":                           "Project Objectives",
#     "autosar objectives":                           "Project Objectives",
#     "autosar safety extensions":                    "Safety Extensions",
#     "safety extensions":                            "Safety Extensions",
#     "security management":                          "Security Management",
#     "secm":                                         "Security Management",
#     "software component template":                  "Software Component Template",
#     "swct":                                         "SWC Template",
#     "swc template":                                 "SWC Template",
#     "standardization template":                     "Standardization Template",
#     "state management":                             "State Management",
#     "sm":                                           "State Management",
#     "swc modeling":                                 "SWC Modeling",
#     "software component modeling":                  "SWC Modeling",
#     "system template":                              "System Template",
#     "syst":                                         "System Template",
#     "time synchronization":                         "Time Synchronization",
#     "timesync":                                     "Time Synchronization",
#     "time sync":                                    "Time Synchronization",
#     "timing extensions":                            "Timing Extensions",
#     "timex":                                        "Timing Extensions",
#     "update and config management":                 "Update and Config Management",
#     "ucm":                                          "UCM",
#     "update configuration management":              "Update and Config Management",

#     # -------------------------------------------------------------------------
#     # SRS – Software Requirement Specification documents (BSW modules)
#     # -------------------------------------------------------------------------
#     "adc driver":                                   "ADC Driver",
#     "adc":                                          "ADC Driver",
#     "analog to digital converter driver":           "ADC Driver",
#     "analog-to-digital converter driver":           "ADC Driver",
#     "bsw general":                                  "BSW General",
#     "basic software general":                       "BSW General",
#     "bus mirroring":                                "Bus Mirroring",
#     "can driver":                                   "CAN Driver",
#     "canif":                                        "CanIf",
#     "can interface":                                "CanIf",
#     "core test":                                    "Core Test",
#     "crypto stack":                                 "Crypto Stack",
#     "cryptographic stack":                          "Crypto Stack",
#     "diagnostics":                                  "Diagnostics",
#     "diag":                                         "Diagnostics",
#     "dio driver":                                   "DIO Driver",
#     "dio":                                          "DIO Driver",
#     "digital i/o driver":                           "DIO Driver",
#     "digital io driver":                            "DIO Driver",
#     "eeprom driver":                                "EEPROM Driver",
#     "eeprom":                                       "EEPROM Driver",
#     "electrically erasable programmable rom driver": "EEPROM Driver",
#     "ethernet":                                     "Ethernet",
#     "eth":                                          "Ethernet",
#     "ethernet driver":                              "Ethernet Driver",
#     "flash driver":                                 "Flash Driver",
#     "fls":                                          "Flash Driver",
#     "flash":                                        "Flash Driver",
#     "flash test":                                   "Flash Test",
#     "flexray":                                      "FlexRay",
#     "fr":                                           "FlexRay",
#     "flex ray":                                     "FlexRay",
#     "free running timer":                           "Free Running Timer",
#     "frt":                                          "Free Running Timer",
#     "gpt":                                          "GPT Driver",
#     "general purpose timer":                        "GPT Driver",
#     "gpt driver":                                   "GPT Driver",
#     "function inhibition manager":                  "Function Inhibition Manager",
#     "fim":                                          "FiM",
#     "gateway":                                      "Gateway",
#     "com gateway":                                  "Gateway",
#     "hw test manager":                              "HW Test Manager",
#     "hardware test manager":                        "HW Test Manager",
#     "htm":                                          "HW Test Manager",
#     "icu driver":                                   "ICU Driver",
#     "icu":                                          "ICU Driver",
#     "input capture unit driver":                    "ICU Driver",
#     "io hw abstraction":                            "IO HW Abstraction",
#     "io hardware abstraction":                      "IO HW Abstraction",
#     "iohwab":                                       "IO HW Abstraction",
#     "i-pdu multiplexer":                            "I-PDU Multiplexer",
#     "ipdu multiplexer":                             "I-PDU Multiplexer",
#     "ipdumux":                                      "I-PDU Multiplexer",
#     "pdu multiplexer":                              "I-PDU Multiplexer",
#     "libraries":                                    "Libraries",
#     "autosar libraries":                            "Libraries",
#     "lin":                                          "LIN",
#     "local interconnect network":                   "LIN",
#     "lin driver":                                   "LIN Driver",
#     "mcu driver":                                   "MCU Driver",
#     "mcu":                                          "MCU Driver",
#     "microcontroller unit driver":                  "MCU Driver",

#     # -------------------------------------------------------------------------
#     # ATS – Acceptance Test Specification documents
#     # -------------------------------------------------------------------------
#     "ats flexray":                                  "ATS FlexRay",
#     "ats communication flexray":                    "ATS FlexRay Communication",
#     "ats communication via bus":                    "ATS Communication Via Bus",
#     "communication via bus":                        "Communication Via Bus",
#     "flexray acceptance test":                      "ATS FlexRay",
#     "bus communication acceptance test":            "ATS Communication Via Bus",
# }

# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 7 — EMBEDDING
# # (uses EMBED_* settings above)
# # ══════════════════════════════════════════════════════════════════════════════

# # ══════════════════════════════════════════════════════════════════════════════
# # STAGE 8 — NEO4J STORAGE
# # ══════════════════════════════════════════════════════════════════════════════

# # kNN: for each chunk, create SIMILAR_TO edges to this many nearest neighbors
# KNN_TOP_K = 10

# # Minimum similarity score to create a SIMILAR_TO edge
# KNN_MIN_SCORE = 0.80

# # Neo4j write batch size (nodes/relationships per transaction)
# NEO4J_BATCH_SIZE = 500

# # ══════════════════════════════════════════════════════════════════════════════
# # ASEI — AGENT LAYER SETTINGS
# # All thresholds, limits, and provider configs for the agentic system.
# # ══════════════════════════════════════════════════════════════════════════════

# # ── Provider API keys (set via environment) ───────────────────────────────────
# GROQ_API_KEY        = os.environ.get("GROQ_API_KEY", "")
# SAMBANOVA_API_KEY   = os.environ.get("SAMBANOVA_API_KEY", "")
# CEREBRAS_API_KEY    = os.environ.get("CEREBRAS_API_KEY", "")
# OPENROUTER_API_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
# BOSCH_API_KEY       = os.environ.get("GEMINI_API_KEY", "")   # shared key for Bosch endpoint
# NVIDIA_API_KEY      = os.environ.get("NVIDIA_API_KEY", "")   # NVIDIA NIM endpoint

# # ── Provider base URLs ────────────────────────────────────────────────────────
# GROQ_BASE_URL       = "https://api.groq.com/openai/v1"
# SAMBANOVA_BASE_URL  = "https://api.sambanova.ai/v1"
# CEREBRAS_BASE_URL   = "https://api.cerebras.ai/v1"
# OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# NVIDIA_BASE_URL     = "https://integrate.api.nvidia.com/v1"   # NVIDIA NIM endpoint
# BOSCH_GPT4O_MINI_URL = (
#     "https://aoai-farm.bosch-temp.com/api/openai/deployments/"
# )

# INTER_AGENT_DELAY_S: int = 60

# NVIDIA_TIMEOUT_BY_MODEL: dict[str, int] = {
#     "qwen/qwen3.5-397b-a17b":                  900,  # 397B — needs most time
#     "deepseek-ai/deepseek-v4-pro":             900,
#     "nvidia/nemotron-3-super-120b-a12b":       600,
#     "qwen/qwen3.5-122b-a10b":                  600,  # 122B — needs time for CoT
#     "mistralai/mistral-medium-3.5-128b":       600,  # reasoning_effort=high needs more time
#     "google/gemma-4-31b-it":                   220,
#     "qwen/qwen3-next-80b-a3b-instruct":        180,
#     "meta/llama-3.3-70b-instruct":             180,
#     "meta/llama-3.1-70b-instruct":             180,
#     "mistralai/ministral-14b-instruct-2512":   180,
#     "mistralai/mixtral-8x22b-instruct-v0.1":   180,
#     "meta/llama-4-maverick-17b-128e-instruct": 120,
#     "microsoft/phi-4-mini-instruct":            90,
#     "nvidia/nemotron-mini-4b-instruct":         90,
#     "google/gemma-3n-e4b-it":                   90,
#     }

# # ── NVIDIA NIM rate-limit & request tuning ────────────────────────────────────
# NVIDIA_TIMEOUT_S         = int(os.environ.get("NVIDIA_TIMEOUT_S",    "600"))
# NVIDIA_RETRIES           = int(os.environ.get("NVIDIA_RETRIES",       "0"))
# NVIDIA_RPM_LIMIT         = int(os.environ.get("NVIDIA_RPM_LIMIT",     "3"))   # free tier = 5 RPM per model
# NVIDIA_MAX_TOKENS        = int(os.environ.get("NVIDIA_MAX_TOKENS",  "1024"))

# NVIDIA_MAX_TOKENS_BY_MODEL = {
# "qwen/qwen3.5-122b-a10b":                  16384,
# "qwen/qwen3.5-397b-a17b":                  16384,  # was 512
# "deepseek-ai/deepseek-v4-pro":             16384,  # was 768
# "nvidia/nemotron-3-super-120b-a12b":       16384,
# "mistralai/mistral-medium-3.5-128b":       16384,  # was 1024
# "qwen/qwen3-next-80b-a3b-instruct":         4096,  # was 1024
# "mistralai/ministral-14b-instruct-2512":    2048,
# "meta/llama-3.3-70b-instruct":              1024,  # correct
# "nvidia/nemotron-mini-4b-instruct":         1024,  # correct
# "meta/llama-3.1-70b-instruct":              1024,  # correct
# "microsoft/phi-4-mini-instruct":            1024,  # correct
# "mistralai/mixtral-8x22b-instruct-v0.1":    1024,  # correct
# "meta/llama-4-maverick-17b-128e-instruct":   512,  # was 1024 (you were over-limit)
# "google/gemma-3n-e4b-it":                    512,  # was 1024 (over-limit)
# }

#     # ── Model names per provider ──────────────────────────────────────────────────

# GROQ_MODEL_HEAVY    = "openai/gpt-oss-120b"       # reasoning, conflict, verification
# GROQ_MODEL_MID      = "qwen/qwen3-32b"             # structured extraction, debate leg
# GROQ_MODEL_FAST     = "llama-3.3-70b-versatile"   # impact, fast debate leg
# SAMBANOVA_MODEL_PRIMARY   = "DeepSeek-V3.2"        # synthesis, hypothesis
# SAMBANOVA_MODEL_FALLBACK  = "DeepSeek-V3.1"        # fallback for primary
# SAMBANOVA_MODEL_MID       = "Meta-Llama-3.3-70B-Instruct"
# CEREBRAS_MODEL      = "llama3.1-8b"                # router, query memory (fastest)
# OPENROUTER_MODEL_CODER  = "qwen/qwen3-coder:free"  # gap detection (262K ctx, formal specs)
# OPENROUTER_MODEL_LONG   = "google/gemma-4-31b-it:free"   # long summarization fallback
# OPENROUTER_MODEL_TINY   = "meta-llama/llama-3.2-3b-instruct:free"  # watchdog / cheap classify
# BOSCH_MODEL         = "gpt-4o-mini"               # Bosch endpoint model alias

# # ── NVIDIA NIM model assignments (by agent role) ──────────────────────────────

# NVIDIA_MODEL_PROSECUTOR    = "qwen/qwen3.5-397b-a17b"
# NVIDIA_MODEL_DEFENDER      = "deepseek-ai/deepseek-v4-pro"
# NVIDIA_MODEL_SKEPTIC       = "meta/llama-3.3-70b-instruct"

# NVIDIA_MODEL_SYNTHESIS     = "qwen/qwen3.5-122b-a10b"
# NVIDIA_MODEL_CONFLICT      = "meta/llama-4-maverick-17b-128e-instruct"
# NVIDIA_MODEL_VERIFICATION  = "meta/llama-3.1-70b-instruct"

# NVIDIA_MODEL_GAP_SUPER     = "nvidia/nemotron-3-super-120b-a12b"
# NVIDIA_MODEL_GAP_PRIMARY   = "mistralai/ministral-14b-instruct-2512"
# NVIDIA_MODEL_GAP_FALLBACK  = "microsoft/phi-4-mini-instruct"

# NVIDIA_MODEL_SUMMARIZATION = "mistralai/mistral-medium-3.5-128b"
# NVIDIA_MODEL_IMPACT        = "qwen/qwen3-next-80b-a3b-instruct"
# NVIDIA_MODEL_IMPACT_FB     = "mistralai/mixtral-8x22b-instruct-v0.1"

# NVIDIA_MODEL_CLASSIFY_FB   = "google/gemma-3n-e4b-it"
# NVIDIA_MODEL_SYNTH_FB      = "nvidia/nemotron-mini-4b-instruct"

# # ── Orchestrator ──────────────────────────────────────────────────────────────
# ASEI_CYCLE_INTERVAL_S   = int(os.environ.get("ASEI_CYCLE_INTERVAL_S", "43200"))  # 12 hour
# ASEI_STATE_DIR          = os.environ.get("ASEI_STATE_DIR", "./output/asei_state")

# # ── Evolution Agent ───────────────────────────────────────────────────────────
# ASEI_STALENESS_DAYS             = int(os.environ.get("ASEI_STALENESS_DAYS", "30"))
# ASEI_LOW_CONFIDENCE_THRESHOLD   = float(os.environ.get("ASEI_LOW_CONFIDENCE_THRESHOLD", "0.70"))

# # ── Conflict Agent ────────────────────────────────────────────────────────────
# ASEI_CONFLICT_STRUCT_LIMIT          = int(os.environ.get("ASEI_CONFLICT_STRUCT_LIMIT", "200"))
# ASEI_CONFLICT_SEMANTIC_LIMIT        = int(os.environ.get("ASEI_CONFLICT_SEMANTIC_LIMIT", "30"))
# ASEI_CONFLICT_SIMILARITY_THRESHOLD  = float(os.environ.get("ASEI_CONFLICT_SIMILARITY_THRESHOLD", "0.92"))

# # ── Synthesis Agent ───────────────────────────────────────────────────────────
# ASEI_SYNTHESIS_CANDIDATE_LIMIT  = int(os.environ.get("ASEI_SYNTHESIS_CANDIDATE_LIMIT", "500"))
# ASEI_SYNTHESIS_LLM_LIMIT        = int(os.environ.get("ASEI_SYNTHESIS_LLM_LIMIT", "40"))
# ASEI_SYNTHESIS_MIN_BRIDGE_COUNT = int(os.environ.get("ASEI_SYNTHESIS_MIN_BRIDGE_COUNT", "2"))
# ASEI_SYNTHESIS_MIN_CONFIDENCE   = float(os.environ.get("ASEI_SYNTHESIS_MIN_CONFIDENCE", "0.70"))

# # ── Verification Agent ────────────────────────────────────────────────────────
# ASEI_VERIFICATION_BATCH         = int(os.environ.get("ASEI_VERIFICATION_BATCH", "20"))
# ASEI_VERIFICATION_REJECT_THRESHOLD = float(os.environ.get("ASEI_VERIFICATION_REJECT_THRESHOLD", "0.40"))

# # ── Reasoning Agent ───────────────────────────────────────────────────────────
# ASEI_REASONING_TOP_K            = int(os.environ.get("ASEI_REASONING_TOP_K", "5"))
# ASEI_REASONING_MAX_HOPS         = int(os.environ.get("ASEI_REASONING_MAX_HOPS", "3"))
# ASEI_REASONING_MIN_SIMILARITY   = float(os.environ.get("ASEI_REASONING_MIN_SIMILARITY", "0.70"))
# ASEI_REASONING_CONTEXT_TOKENS   = int(os.environ.get("ASEI_REASONING_CONTEXT_TOKENS", "6000"))
# ASEI_REASONING_DEBATE_WEIGHT_HEAVY  = float(os.environ.get("ASEI_REASONING_DEBATE_WEIGHT_HEAVY", "0.45"))
# ASEI_REASONING_DEBATE_WEIGHT_MID    = float(os.environ.get("ASEI_REASONING_DEBATE_WEIGHT_MID", "0.35"))
# ASEI_REASONING_DEBATE_WEIGHT_LOCAL  = float(os.environ.get("ASEI_REASONING_DEBATE_WEIGHT_LOCAL", "0.20"))

# # ── Summarization Agent ───────────────────────────────────────────────────────
# ASEI_SUMMARY_MAX_CHUNKS_PER_MODULE  = int(os.environ.get("ASEI_SUMMARY_MAX_CHUNKS_PER_MODULE", "30"))
# ASEI_SUMMARY_CONTEXT_CHARS          = int(os.environ.get("ASEI_SUMMARY_CONTEXT_CHARS", "8000"))

# # ── Gap Detection Agent ───────────────────────────────────────────────────────
# ASEI_GAP_CANDIDATE_LIMIT        = int(os.environ.get("ASEI_GAP_CANDIDATE_LIMIT", "50"))
# ASEI_GAP_MIN_CONFIDENCE         = float(os.environ.get("ASEI_GAP_MIN_CONFIDENCE", "0.65"))

# # ── Impact Agent ──────────────────────────────────────────────────────────────
# ASEI_IMPACT_MAX_HOPS            = int(os.environ.get("ASEI_IMPACT_MAX_HOPS", "4"))
# ASEI_IMPACT_BATCH               = int(os.environ.get("ASEI_IMPACT_BATCH", "50"))

# # ── Watchdog Agent ────────────────────────────────────────────────────────────
# ASEI_WATCHDOG_REJECTION_CEILING     = float(os.environ.get("ASEI_WATCHDOG_REJECTION_CEILING", "0.40"))
# ASEI_WATCHDOG_ERROR_CEILING         = float(os.environ.get("ASEI_WATCHDOG_ERROR_CEILING", "0.20"))

# # ── Query Memory Agent ────────────────────────────────────────────────────────
# ASEI_QUERY_MEMORY_LOW_CONF_THRESHOLD    = float(os.environ.get("ASEI_QUERY_MEMORY_LOW_CONF_THRESHOLD", "0.60"))
# ASEI_QUERY_MEMORY_HOT_SPOT_COUNT        = int(os.environ.get("ASEI_QUERY_MEMORY_HOT_SPOT_COUNT", "3"))



"""
config/settings.py
==================
Single source of truth for every tunable parameter in the pipeline.
Edit this file to adapt the pipeline to your specific AUTOSAR corpus.
"""

from __future__ import annotations
import os
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE VERSION
# Bump this string whenever the extraction logic or schema changes.
# Stored on every node as `pipeline_version` for future ASEI drift detection.
# ══════════════════════════════════════════════════════════════════════════════

PIPELINE_VERSION = os.environ.get("PIPELINE_VERSION", "1.1.0")

# ══════════════════════════════════════════════════════════════════════════════
# NEO4J
# ══════════════════════════════════════════════════════════════════════════════

NEO4J_URI      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.environ.get("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD",  "autosar123")

# ══════════════════════════════════════════════════════════════════════════════
# vLLM / LLM
# ══════════════════════════════════════════════════════════════════════════════

VLLM_BASE_URL  = os.environ.get("VLLM_URL", "http://localhost:8011/v1")
VLLM_API_KEY   = "dummy"                    # vLLM ignores the key
LLM_MODEL      = "Qwen/Qwen2.5-72B-Instruct-AWQ"
LLM_TEMPERATURE        = 0                  # deterministic for extraction
LLM_MAX_TOKENS         = 8192              # enough for entity extraction JSON
LLM_TIMEOUT            = 600               # seconds per request
LLM_MAX_CONCURRENT     = 16              # match --max-num-seqs 16

# ══════════════════════════════════════════════════════════════════════════════
# EMBEDDINGS
# ══════════════════════════════════════════════════════════════════════════════

EMBED_MODEL    = "BAAI/bge-m3"
EMBED_DIM      = 1024
EMBED_BATCH_SIZE_GPU = 16
EMBED_BATCH_SIZE_CPU = 8
EMBED_NORMALIZE      = True

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — PDF EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

# Fraction of page height to crop as header / footer
PDF_HEADER_MARGIN = 0.10   # 12% top  — AUTOSAR docs have large headers
PDF_FOOTER_MARGIN = 0.08   # 10% bottom — page numbers + "AUTOSAR confidential"

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — NOISE REMOVAL
# ══════════════════════════════════════════════════════════════════════════════

# A line appearing on this fraction of pages or more = running header/footer
REPEATED_LINE_THRESHOLD   = 0.30

# If this fraction of lines on a page match TOC pattern = TOC page → drop
TOC_LINE_RATIO_THRESHOLD  = 0.50

# If this fraction of lines contain date/version patterns = revision page
REVISION_LINE_RATIO       = 0.40

# Pages with fewer than this many chars after cleaning = near-blank → drop
MIN_PAGE_CHARS            = 120

# Lines shorter than this starting with Figure/Table/etc = orphaned caption
CAPTION_MAX_LEN           = 65

# Cross-document boilerplate: cosine similarity above this = same boilerplate
BOILERPLATE_SIM_THRESHOLD = 0.92

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — REQUIREMENT ID HARVESTING
# ══════════════════════════════════════════════════════════════════════════════

# Max IDs per page before skipping cross-ref pair generation (index page guard)
MAX_IDS_PER_PAGE_FOR_XREF = 30

# Regex patterns for AUTOSAR IDs — add more as needed for your corpus

REQUIREMENT_ID_PATTERNS = [
    # --- Existing patterns ---
    r"\[SWS_[A-Za-z]+_\d{5}\]",        # Software Specification (SWS)
    r"\[SRS_[A-Za-z]+_\d{5}\]",        # Software Requirements Spec (SRS)
    r"\[constr_\d{4}\]",                # Constraints
    r"\[ECUC_[A-Za-z]+_\d{5}\]",       # ECU Configuration parameters
    r"\[BSW_\d{5}\]",                   # Basic Software requirements
    r"\[ASWS_[A-Za-z]+_\d{5}\]",       # Application SW requirements

    # --- Requirements Specification (RS) ---
    r"\[RS_[A-Za-z]+_\d{5}\]",         # General RS requirements

    # --- Protocol Specification (PRS) ---
    r"\[PRS_[A-Za-z]+_\d{5}\]",        # Protocol requirements (SOME/IP, NM, TimeSync, etc.)

    # --- Acceptance Test Specification (ATS) ---
    r"\[ATS_[A-Za-z]+_\d{5}\]",        # Acceptance test requirements

    # --- Explanatory Documents (EXP) ---
    r"\[EXP_[A-Za-z]+_\d{5}\]",        # Explanatory document refs

    # --- Safety & Security ---
    r"\[SafetyReq_[A-Za-z]+_\d{5}\]",  # Functional safety requirements
    r"\[SecReq_[A-Za-z]+_\d{5}\]",     # Security requirements
    r"\[IAM_[A-Za-z]+_\d{5}\]",        # Identity and Access Management

    # --- Diagnostics ---
    r"\[DiagReq_[A-Za-z]+_\d{5}\]",    # Diagnostic requirements
    r"\[DEM_[A-Za-z]+_\d{5}\]",        # Diagnostic Event Manager
    r"\[DCM_[A-Za-z]+_\d{5}\]",        # Diagnostic Communication Manager

    # --- Communication (SOME/IP, COM, NM) ---
    r"\[SOMEIP_[A-Za-z]+_\d{5}\]",     # SOME/IP protocol
    r"\[COM_[A-Za-z]+_\d{5}\]",        # Communication requirements
    r"\[NM_[A-Za-z]+_\d{5}\]",         # Network Management
    r"\[E2E_[A-Za-z]+_\d{5}\]",        # End-to-End protection

    # --- Execution & State Management ---
    r"\[ExecReq_[A-Za-z]+_\d{5}\]",    # Execution Management
    r"\[SM_[A-Za-z]+_\d{5}\]",         # State Management
    r"\[PHM_[A-Za-z]+_\d{5}\]",        # Platform Health Management

    # --- Cryptography & Security ---
    r"\[Crypto_[A-Za-z]+_\d{5}\]",     # Cryptography requirements

    # --- Persistency, Log & Trace ---
    r"\[PER_[A-Za-z]+_\d{5}\]",        # Persistency
    r"\[LOG_[A-Za-z]+_\d{5}\]",        # Log and Trace

    # --- Time Synchronization ---
    r"\[TS_[A-Za-z]+_\d{5}\]",         # Time Synchronization

    # --- Update & Config Management ---
    r"\[UCM_[A-Za-z]+_\d{5}\]",        # Update and Config Management

    # --- Driver / Hardware Abstraction (SRS_*Driver) ---
    r"\[ADC_[A-Za-z]+_\d{5}\]",        # ADC Driver
    r"\[DIO_[A-Za-z]+_\d{5}\]",        # DIO Driver
    r"\[GPT_[A-Za-z]+_\d{5}\]",        # GPT Driver
    r"\[ICU_[A-Za-z]+_\d{5}\]",        # ICU Driver
    r"\[MCU_[A-Za-z]+_\d{5}\]",        # MCU Driver
    r"\[PWM_[A-Za-z]+_\d{5}\]",        # PWM Driver (common in AUTOSAR)
    r"\[SPI_[A-Za-z]+_\d{5}\]",        # SPI Driver (common in AUTOSAR)
    r"\[IOHW_[A-Za-z]+_\d{5}\]",       # IO Hardware Abstraction

    # --- Flexible / Generic fallback ---
    r"\[[A-Z][A-Za-z0-9]+_[A-Za-z]+_\d{5}\]",  # Any AUTOSAR-style bracketed ID

    r"\[TR_[A-Za-z]+_\d{5}\]",   # Technical Report spec items (e.g. TR_OSTI_00001)
    r"\bISO\s+\d{4,5}(?:[-:]\d+)?\b",   # ISO standard refs (ISO 23150, ISO 23150:2021, ISO 26262)
    # --- Technical Report (TR) - CRITICAL: covers entire OS Tracing domain ---
    r"\[TR_[A-Za-z0-9]+_\d{5}\]",       # TR_OSTI_00001 etc. (note: [A-Za-z0-9] not [A-Za-z])

    # --- Driver / HW Abstraction - Missing drivers ---
    r"\[FLS_[A-Za-z0-9]+_\d{5}\]",      # Flash Driver / Flash Test
    r"\[EEP_[A-Za-z0-9]+_\d{5}\]",      # EEPROM Driver
    r"\[ETH_[A-Za-z0-9]+_\d{5}\]",      # Ethernet
    r"\[LIN_[A-Za-z0-9]+_\d{5}\]",      # LIN Driver
    r"\[CAN_[A-Za-z0-9]+_\d{5}\]",      # CAN Driver
    r"\[FR_[A-Za-z0-9]+_\d{5}\]",       # FlexRay Driver
    r"\[FIM_[A-Za-z0-9]+_\d{5}\]",      # Function Inhibition Manager
    r"\[GW_[A-Za-z0-9]+_\d{5}\]",       # Gateway
    r"\[BM_[A-Za-z0-9]+_\d{5}\]",       # Bus Mirroring
    r"\[PDUR_[A-Za-z0-9]+_\d{5}\]",     # PDU Router / I-PDU Multiplexer
    r"\[FRT_[A-Za-z0-9]+_\d{5}\]",      # Free Running Timer
    r"\bISO\s+\d{4,5}(?:[-:]\d+)?\b",   # ISO standard refs — not in Doc 3 at all
]
# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — CHUNKING
# ══════════════════════════════════════════════════════════════════════════════

CHUNK_MAX_TOKENS      = 512    # Max tokens per chunk
CHUNK_OVERLAP_TOKENS  = 128     # Overlap when splitting oversized chunks
CHUNK_MIN_TOKENS      = 30     # Drop chunks smaller than this
CHUNK_TABLE_MAX_TOKENS= 1200    # Tables may exceed normal max — kept whole
MIN_UNIQUE_WORD_RATIO = 0.15   # Drop chunks with low lexical diversity

# Heading levels to split on

SPLIT_HEADERS = [
    ("#",     "H1"),
    ("##",    "H2"),
    ("###",   "H3"),
    ("####",  "H4"),
    ("#####", "H5"),
]

# ══════════════════════════════════════════════════════════════════════════════
# ONTOLOGY GOVERNANCE POLICY
# ══════════════════════════════════════════════════════════════════════════════
# Strict structural labels are authoritative, deterministically extracted, and 
# managed entirely by the physical ingestion track (Track A). The LLM is 
# FORBIDDEN from creating these directly.
STRICT_STRUCTURAL_LABELS = {
    "Document",
    "Chunk",
    "Corpus"
}

# If the LLM violates the ontology boundary and emits a structural label, 
# we non-destructively downcast it to a semantic/referential equivalent.
COERCION_MAP = {
    "Document": "DocumentRef",
    "Chunk": "Concept",
    "Corpus": "Concept",
}

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — ENTITY & RELATION EXTRACTION SCHEMA
# ══════════════════════════════════════════════════════════════════════════════
# Customize these to match your specific AUTOSAR document corpus.
# Read 20-30 pages of your actual docs first, then adjust.

ALLOWED_NODES = [
    # --- Your originals (unchanged) ---
    "Requirement",        # [SWS_X_NNNNN], [SRS_X_NNNNN], [TR_X_NNNNN], [AP_TPS_*] etc.
    # "Document",           # First-class AUTOSAR PDF/document entity
    "ConfigParameter",    # ECUC parameter definitions, struct fields, enum values
    "Module",             # AUTOSAR SW module (ComM, NvM, Can, Dcm, CSM, CRYIF ...)
    "Concept",            # Abstract AUTOSAR concept (PDU, Signal, Frame, Job, Key, Task ...)
    "StandardRef",        # External standard (ISO 26262, POSIX, MISRA, AUTOSAR_SRS_General)
    "DocumentRef",        # Another AUTOSAR document referenced from this one
    "Function",           # Software function or API (ArtiInit(), Csm_MacGenerate(), Rte_Call_* ...)
    "System",             # System or sub-system (Adaptive Platform, Classic Platform, ECU)
    "Organization",       # Company or standards body (AUTOSAR, OEM, Tier-1)

    # --- Added: design & model elements ---
    "FunctionalCluster",  # AP-specific named cluster (Execution Management, Communication Management ...)
    "DataType",           # Type definition: struct, enum, typedef (ArtiVersionInfoType, CallingContext ...)
    "Class",              # Meta-model class defined in a TPS doc (ApmcFunctionalClusterDef, Process ...)
    "Category",           # AUTOSAR category value (STANDARDIZED_CLUSTER_DEFINITION, VENDOR_SPECIFIC ...)

    # --- Added: document structure ---
    "SpecificationItem",  # Numbered spec item block ⌈...⌋ that may wrap one or more requirements
    "ChangeRecord",       # Entry in Document Change History (release, changed-by, description)
    "Constraint",         # Modelling constraint ([constr_NNNN], binding-time rule, M1/M2 check)

    "TestSpecification",   # ATS / test-spec documents
    "TestCase",            # individual test cases inside ATS / test specs

    # --- Added: gap detection agent output ---
    "SpecGap",             # Knowledge/specification gap node written by gap_detection_agent

    "UseCase",   # Formal use-case specification with stakeholder, goal, constraints (AP EXP sensor docs)
    "APIParameter",  # Typed formal parameter of an API function (name, type, direction) — from TR 1083 API spec tables

    "Protocol",           # 7 PRS docs define complete protocol specs: SOME/IP, SOME/IP-SD,
                          # E2E, NM Protocol, Log and Trace Protocol, TimeSync Protocol,
                          # Testability Protocol.

    "MessageFormat",      # PRS_SOMEIPProtocol, PRS_E2EProtocol, PRS_NMProtocol define named
                          # message/frame formats with field layouts, sizes, byte offsets.
                          # Distinct from DataType (programming type) and Concept (abstract).

    "AIDomain",           # 6 EXP_AI* documents: AIBodyAndComfort, AIChassis,
                          # AIHMIMultimediaAndTelematics, AIOccupantAndPedestrianSafety,
                          # AIPowertrain, AIUserGuide. AI application domains with specific
                          # sensor/actuator interfaces and safety requirements.
    "SpecGap",            # Knowledge/specification gap node written by gap_detection_agent
                          # — not present in Doc 4 at all
    'SensorInterface',
    'TracingInterface',
]

ALLOWED_RELATIONSHIPS = [
    # --- Your originals (unchanged) ---
    "REFERENCES",         # requirement/concept references another item
    "IMPLEMENTS",         # module/function implements a requirement
    "DEFINED_BY",         # concept/requirement defined by a standard or document
    "DEPENDS_ON",         # module/requirement depends on another module/requirement
    "ALLOCATED_TO",       # requirement allocated to a module or functional cluster
    "CONFIGURES",         # config parameter configures a module or behavior
    "SPECIALIZES",        # concept specializes a more abstract concept (subtype)
    "DERIVED_FROM",       # requirement derived from a parent requirement (vertical trace)
    "CONTRADICTS",        # LLM-inferred semantic conflict between two items
    "TRACES_TO",          # SRS → SWS forward tracing
    "HAS_PARAMETER",      # module/function has a config parameter or typed parameter
    "TESTED_BY",          # requirement tested by a test case or test specification
    "REFINES",            # more specific version of a sibling requirement or concept

    # --- Added: implementation & call chain ---
    "CALLS",              # function directly calls another function (precise API call chain,
                          # e.g. Csm_MacGenerate → CryIf_ProcessJob → Crypto_ProcessJob)
                          # distinct from DEPENDS_ON which is module-level architectural coupling

    # --- Added: ownership & composition ---
    "OWNED_BY",           # document or module is owned by an organization or functional cluster
    "PART_OF",            # module is part of a system; cluster is part of AP/CP; field is part of struct
    "CONTAINS",           # document contains a SpecificationItem; module contains a sub-module
    "HAS_CONFIG",         # module has a configuration container or parameter set (coarser than HAS_PARAMETER)

    # --- Added: taxonomy ---
    "INSTANCE_OF",        # a modelled element is an instance of a meta-model class
    "HAS_CATEGORY",       # class or concept has an allowed category value

    # --- Added: document lifecycle ---
    "CHANGED_IN",         # spec item or document was added/changed/deleted in a given release

    # --- Added: definition location ---
    "DEFINED_IN",         # concept, class, or data type is formally defined in a specific document
                          # complements DEFINED_BY: DEFINED_BY points to the authority (standard/org),
                          # DEFINED_IN points to the exact document containing the formal definition

    "PRECEDES",   # Function/hook call must precede another in execution sequence (API ordering constraint, e.g. ArtiVersionInfo → ArtiInit → ArtiTaskInfo)
    "MAPS_TO",    # ISO/external standard data structure or concept maps to an AUTOSAR service or interface element (e.g. ISO 23150 object list → ara::adi tracking service)
    "COMPLIES_WITH",      # AUTOSAR spec makes a conformance claim against an external standard.
                          # RS_SafetyExtensions COMPLIES_WITH ISO 26262.
                          # RS_Cryptography COMPLIES_WITH NIST/FIPS standards.
                          # EXP_IPsecImplementationGuidelines COMPLIES_WITH RFC 4301.
                          # EXP_SensorInterfaces COMPLIES_WITH ISO 23150.
                          # Distinct from REFERENCES (general) and DEFINED_BY (standard defines concept).

    "TRANSPORTED_BY",     # A Signal or PDU is transported by a specific protocol or frame format.
                          # E.g. NM PDU TRANSPORTED_BY FlexRay Frame or CAN Frame.
                          # SRS_Gateway and SRS_IPDUMultiplexer define routing rules for this.
                          # Distinct from PART_OF (structural) — TRANSPORTED_BY is runtime/physical.


]
# ══════════════════════════════════════════════════════════════════════════════
# STAGE 6 — ENTITY RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

ENTITY_RESOLUTION_THRESHOLD       = 0.92  # Cosine similarity for same-entity clustering (certain)
ENTITY_RESOLUTION_UNCERTAIN_LOW   = 0.75  # Lower bound of uncertain zone — sent to LLM
ENTITY_RESOLUTION_UNCERTAIN_HIGH  = 0.92  # Upper bound of uncertain zone — sent to LLM

# Manual canonical name overrides — common AUTOSAR abbreviation variants.
# # Keys are lowercased for matching. Extend with your corpus-specific variants.

CANONICAL_NAME_OVERRIDES: dict[str, str] = {
    # -------------------------------------------------------------------------
    # Module name variants (original)
    # -------------------------------------------------------------------------

    # ── SOME/IP Header & Service Discovery ───────────────────────────────────
    "some/ip header":                           "SOME/IP Header",
    "someip header":                            "SOME/IP Header",
    "someip-sd":                                "SOME/IP-SD",
    "someip sd":                                "SOME/IP-SD",
    "some/ip service discovery":                "SOME/IP-SD",
    "service id":                               "Service ID",
    "method id":                                "Method ID",
    "client id":                                "Client ID",
    "session id":                               "Session ID",
    "message type":                             "Message Type",
    "return code":                              "Return Code",
    "request/response":                         "Request/Response",
    "fire and forget":                          "Fire and Forget",
    "notification":                             "Notification",
    "offer service":                            "OfferService",
    "find service":                             "FindService",
    "subscribe eventgroup":                     "SubscribeEventgroup",

    # ── E2E Protocol ─────────────────────────────────────────────────────────
    "e2e profile 1":                            "E2E Profile 1",
    "e2e profile 2":                            "E2E Profile 2",
    "e2e profile 4":                            "E2E Profile 4",
    "e2e profile 5":                            "E2E Profile 5",
    "e2e profile 6":                            "E2E Profile 6",
    "e2e profile 7":                            "E2E Profile 7",
    "e2e_p1configtype":                         "E2E_P1ConfigType",
    "e2e_p2configtype":                         "E2E_P2ConfigType",
    "e2e_pxxx configtype":                      "E2E_PConfigType",
    "data id":                                  "Data ID",
    "counter":                                  "Counter",
    "crc":                                      "CRC",
    "cyclic redundancy check":                  "CRC",

    # ── Network Management Protocol ───────────────────────────────────────────
    "nm pdu":                                   "NM PDU",
    "nm message":                               "NM PDU",
    "node id":                                  "Node ID",
    "nm coordinator":                           "NM Coordinator",
    "nm state machine":                         "NM State Machine",
    "bus sleep mode":                           "Bus-Sleep Mode",
    "prepare bus sleep":                        "Prepare Bus-Sleep",
    "normal operation":                         "Normal Operation",
    "repeat message":                           "Repeat Message",

    # ── Time Sync Protocol ────────────────────────────────────────────────────
    "time master":                              "Time Master",
    "time slave":                               "Time Slave",
    "sync master":                              "Sync Master",
    "sync slave":                               "Sync Slave",
    "time domain":                              "Time Domain",
    "global time":                              "Global Time",
    "timesync message":                         "TimeSync Message",
    "follow_up":                                "Follow_Up",
    "pdelay_req":                               "Pdelay_Req",
    "pdelay_resp":                              "Pdelay_Resp",
    "offset time domain":                       "Offset Time Domain",
    "ets":                                      "ETS",
    "egress time stamp":                        "Egress Timestamp",
    "ingress time stamp":                       "Ingress Timestamp",

    # ── Log and Trace Protocol ────────────────────────────────────────────────
    "dlt message":                              "DLT Message",
    "dlt header":                               "DLT Header",
    "extended header":                          "DLT Extended Header",
    "verbose mode":                             "Verbose Mode",
    "non-verbose mode":                         "Non-Verbose Mode",
    "app id":                                   "Application ID",
    "ctx id":                                   "Context ID",
    "ecu id":                                   "ECU ID",

    # ── Execution Management ──────────────────────────────────────────────────
    "function group":                           "Function Group",
    "function group state":                     "Function Group State",
    "off state":                                "Off State",
    "startup state":                            "Startup State",
    "shutdown state":                           "Shutdown State",
    "machine state":                            "Machine State",
    "exec management":                          "Execution Management",
    "execution dependency":                     "Execution Dependency",
    "scheduling policy":                        "Scheduling Policy",
    "resource group":                           "Resource Group",

    # ── State Management ──────────────────────────────────────────────────────
    "trigger in port":                          "TriggerInPort",
    "notifier port":                            "NotifierPort",

    # ── Platform Health Management ────────────────────────────────────────────
    "phm supervised entity":                    "PHM Supervised Entity",
    "alive supervision":                        "Alive Supervision",
    "deadline supervision":                     "Deadline Supervision",
    "logical supervision":                      "Logical Supervision",
    "checkpoint":                               "Checkpoint",
    "global supervision":                       "Global Supervision",
    "health channel":                           "Health Channel",

    # ── Identity and Access Management ───────────────────────────────────────
    "access control":                           "Access Control",
    "permission":                               "Permission",
    "grant":                                    "Grant",
    "principal":                                "Principal",

    # ── Update and Config Management ─────────────────────────────────────────
    "ucm master":                               "UCM Master",
    "ucm subordinate":                          "UCM Subordinate",
    "software package":                         "Software Package",
    "software cluster":                         "Software Cluster",
    "transfer":                                 "Transfer",
    "activation":                               "Activation",
    "rollback":                                 "Rollback",

    # ── Persistency ───────────────────────────────────────────────────────────
    "key-value storage":                        "Key-Value Storage",
    "file proxy":                               "File Proxy",
    "persistency port":                         "Persistency Port",
    "redundancy":                               "Redundancy",

    # ── Log and Trace (RS) ────────────────────────────────────────────────────
    "log sink":                                 "Log Sink",
    "log message":                              "Log Message",
    "log level":                                "Log Level",
    "fatal":                                    "FATAL",
    "verbose":                                  "VERBOSE",

    # ── Security Management ───────────────────────────────────────────────────
    "secure onboard communication":             "SecOC",
    "freshness value":                          "Freshness Value",
    "mac verification":                         "MAC Verification",
    "truncated mac":                            "Truncated MAC",
    "tls":                                      "TLS",

    # ── Communication Management ──────────────────────────────────────────────
    "service interface":                        "Service Interface",
    "skeleton":                                 "Skeleton",
    "proxy":                                    "Proxy",
    "event":                                    "Event",
    "field":                                    "Field",
    "method":                                   "Method",
    "someip binding":                           "SOME/IP Binding",
    "ipc binding":                              "IPC Binding",
    "dds binding":                              "DDS Binding",
    "signal based binding":                     "Signal-Based Binding",
    "service discovery":                        "Service Discovery",

    # ── Network Management (RS) ───────────────────────────────────────────────
    "cannm":                                    "CanNm",
    "can nm":                                   "CanNm",
    "flexray nm":                               "FrNm",
    "udp nm":                                   "UdpNm",
    "udpnm":                                    "UdpNm",
    "generic nm":                               "GenericNm",

    # ── C++14 Guidelines ──────────────────────────────────────────────────────
    "autosar c++ 14 guidelines":                "C++14 Guidelines",
    "autosar cpp guidelines":                   "C++14 Guidelines",
    "rule a":                                   "AUTOSAR C++ Rule",
    "misra c++":                                "MISRA C++",

    # ── Cryptography / Crypto Stack ───────────────────────────────────────────
    "aes":                                      "AES",
    "aes-128":                                  "AES-128",
    "aes-256":                                  "AES-256",
    "rsa":                                      "RSA",
    "ecdsa":                                    "ECDSA",
    "sha-256":                                  "SHA-256",
    "sha-512":                                  "SHA-512",
    "hmac":                                     "HMAC",
    "cmac":                                     "CMAC",
    "drbg":                                     "DRBG",
    "random number generator":                  "RNG",
    "key derivation":                           "Key Derivation",
    "certificate":                              "Certificate",
    "x.509":                                    "X.509",

    # ── Manifest Specification / System Template ──────────────────────────────
    "arxml":                                    "ARXML",
    "m1 level":                                 "M1 Level",
    "m2 level":                                 "M2 Level",
    "autosar meta model":                       "AUTOSAR Meta Model",
    "service manifest":                         "Service Manifest",
    "machine manifest":                         "Machine Manifest",

    # ── SWC Modeling ──────────────────────────────────────────────────────────
    "swc":                                      "SWC",
    "software component":                       "SWC",
    "atomic swc":                               "Atomic SWC",
    "composition":                              "Composition",
    "port interface":                           "Port Interface",
    "sender receiver":                          "Sender-Receiver",
    "client server":                            "Client-Server",
    "r-port":                                   "R-Port",
    "p-port":                                   "P-Port",
    "required port":                            "R-Port",
    "provided port":                            "P-Port",
    "runnable":                                 "Runnable",
    "swc internal behavior":                    "SwcInternalBehavior",
    "inter runnable variable":                  "IRV",
    "rte event":                                "RTE Event",
    "timing event":                             "TimingEvent",
    "data received event":                      "DataReceivedEvent",

    # ── SRS Driver docs ───────────────────────────────────────────────────────
    "adc channel":                              "ADC Channel",
    "adc group":                                "ADC Group",
    "adc result":                               "ADC Result",
    "adc hw unit":                              "ADC HW Unit",
    "pwm channel":                              "PWM Channel",
    "pwm period":                               "PWM Period",
    "pwm duty cycle":                           "PWM Duty Cycle",
    "gpt channel":                              "GPT Channel",
    "gpt predef timer":                         "GPT Predef Timer",
    "icu channel":                              "ICU Channel",
    "icu edge":                                 "ICU Edge",
    "mcu pll":                                  "MCU PLL",
    "mcu mode":                                 "MCU Mode",
    "flash sector":                             "Flash Sector",
    "flash page":                               "Flash Page",
    "eeprom block":                             "EEPROM Block",
    "lin frame":                                "LIN Frame",
    "lin channel":                              "LIN Channel",
    "flexray cluster":                          "FlexRay Cluster",
    "flexray channel":                          "FlexRay Channel",
    "flexray slot":                             "FlexRay Slot",
    "can controller":                           "CAN Controller",
    "can mailbox":                              "CAN Mailbox",
    "can frame":                                "CAN Frame",
    "ethernet controller":                      "Ethernet Controller",
    "ethernet frame":                           "Ethernet Frame",
    "mac address":                              "MAC Address",

    # ── SRS Diagnostics ───────────────────────────────────────────────────────
    "diagnostic session":                       "Diagnostic Session",
    "default session":                          "Default Session",
    "programming session":                      "Programming Session",
    "extended session":                         "Extended Diagnostic Session",
    "dtc":                                      "DTC",
    "diagnostic trouble code":                  "DTC",
    "uds":                                      "UDS",
    "unified diagnostic services":              "UDS",
    "obd":                                      "OBD",
    "on-board diagnostics":                     "OBD",
    "negative response code":                   "Negative Response Code",
    "nrc":                                      "NRC",
    "did":                                      "DID",
    "data identifier":                          "DID",
    "routine control":                          "Routine Control",
    "security access":                          "Security Access",

    # ── SRS Gateway / COM / I-PDU Multiplexer ────────────────────────────────
    "n-pdu":                                    "N-PDU",
    "signal group":                             "Signal Group",
    "routing path":                             "Routing Path",
    "gateway mapping":                          "Gateway Mapping",
    "com signal":                               "COM Signal",
    "i-signal":                                 "I-Signal",
    "system signal":                            "System Signal",
    "transfer property":                        "Transfer Property",
    "triggered":                                "Triggered",
    "pending":                                  "Pending",

    # ── Function Inhibition Manager ───────────────────────────────────────────
    "fim function id":                          "FiM FunctionID",
    "fim inhibition mask":                      "FiM InhibitionMask",
    "fim event id":                             "FiM EventID",
    "sum inhibition":                           "SUM Inhibition",
    "last failed inhibition":                   "LastFailed Inhibition",

    # ── Transformer ───────────────────────────────────────────────────────────
    "transformer":                              "Transformer",
    "transformer chain":                        "Transformer Chain",
    "e2e transformer":                          "E2E Transformer",
    "com based transformer":                    "COM-Based Transformer",
    "serializer":                               "Serializer",
    "deserializer":                             "Deserializer",
    "buffer handling":                          "Buffer Handling",

    # ── ATS documents ─────────────────────────────────────────────────────────
    "test precondition":                        "Test Precondition",
    "test step":                                "Test Step",
    "expected result":                          "Expected Result",
    "test verdict":                             "Test Verdict",
    "pass":                                     "PASS",
    "fail":                                     "FAIL",
    "inconclusive":                             "INCONCLUSIVE",
    "test suite":                               "Test Suite",

    # ── EXP_AI documents ──────────────────────────────────────────────────────
    "ai function":                              "AI Function",
    "ml model":                                 "ML Model",
    "inference":                                "Inference",
    "adas":                                     "ADAS",
    "advanced driver assistance systems":       "ADAS",
    "perception":                               "Perception",
    "object detection":                         "Object Detection",
    "scene understanding":                      "Scene Understanding",
    "occupant monitoring":                      "Occupant Monitoring",
    "hmi":                                      "HMI",
    "human machine interface":                  "HMI",

    # ── EXP_ARAComAPI ─────────────────────────────────────────────────────────
    "find service handler":                     "FindServiceHandler",
    "service handle":                           "ServiceHandle",
    "sample":                                   "Sample",
    "subscription handler":                     "SubscriptionHandler",
    "get new samples":                          "GetNewSamples",

    # ── Interrupt Handling ────────────────────────────────────────────────────
    "isr category 1":                           "ISR Category 1",
    "isr category 2":                           "ISR Category 2",
    "interrupt lock":                           "Interrupt Lock",
    "os application":                           "OS Application",

    # ── Parallel Processing ───────────────────────────────────────────────────
    "spinlock":                                 "Spinlock",
    "barrier":                                  "Barrier",
    "core assignment":                          "Core Assignment",
    "data consistency":                         "Data Consistency",
    "race condition":                           "Race Condition",

    # ── NV Data Handling ──────────────────────────────────────────────────────
    "nv block":                                 "NV Block",
    "nv data":                                  "NV Data",
    "nvm block":                                "NvM Block",
    "write all":                                "WriteAll",
    "read all":                                 "ReadAll",
    "immediate write":                          "Immediate Write",

    # ── Feature Model Exchange Format ─────────────────────────────────────────
    "feature model":                            "Feature Model",
    "feature":                                  "Feature",
    "feature constraint":                       "Feature Constraint",

    # ── Timing Extensions ─────────────────────────────────────────────────────
    "timing constraint":                        "Timing Constraint",
    "age constraint":                           "Age Constraint",
    "latency constraint":                       "Latency Constraint",
    "execution time constraint":                "Execution Time Constraint",
    "synchronization constraint":               "Synchronization Constraint",
    "timing event chain":                       "Timing Event Chain",

    # ── Adaptive Platform Machine Configuration ───────────────────────────────
    "adaptive platform machine":                "AP Machine",
    "machine design":                           "Machine Design",
    "os module instantiation":                  "OS Module Instantiation",
    "network endpoint":                         "Network Endpoint",
    "ethernet port":                            "Ethernet Port",
    "tls config":                               "TLS Configuration",

    # ── OS Tracing Interface (TR 1083) ───────────────────────────────────────────
    "arti":                                          "ARTI",
    "arti tracing interface":                        "ARTI",
    "os/arti adapter":                               "OS/ARTI Adapter",
    "os/arti driver":                                "OS/ARTI Driver",
    "arti adapter":                                  "OS/ARTI Adapter",
    "artitaskswitch":                                "ArtiTaskSwitch",
    "artitaskwait":                                  "ArtiTaskWait",
    "artitaskrelease":                               "ArtiTaskRelease",
    "artitaskpreempt":                               "ArtiTaskPreempt",
    "artitaskexit":                                  "ArtiTaskExit",
    "artitaskcreate":                                "ArtiTaskCreate",
    "artitaskrename":                                "ArtiTaskRename",
    "artitaskinfo":                                  "ArtiTaskInfo",
    "artiprocessswitch":                             "ArtiProcessSwitch",
    "artiprocesscreate":                             "ArtiProcessCreate",
    "artiprocessdestroy":                            "ArtiProcessDestroy",
    "artiprocessrename":                             "ArtiProcessRename",
    "artiprocessinfo":                               "ArtiProcessInfo",
    "artiversioninfo":                               "ArtiVersionInfo",
    "artiinit":                                      "ArtiInit",
    "articleanup":                                   "ArtiCleanup",
    "artiversioninfotype":                           "ArtiVersionInfoType",
    "callingcontext":                                "CallingContext",
    "kinterruptsdisabled":                           "kInterruptsDisabled",
    "kinterruptsmaybedisabled":                      "kInterruptsMayBeDisabled",
    "kinterruptsmaybedisabled":                      "kInterruptsMayNotBeDisabled",
    "rs_osi_00210":                                  "RS_OSI_00210",
    "modelled process":                              "Modelled Process",
    "execution manifest":                            "Execution Manifest",

    # ── Sensor Interfaces (AP EXP 913) ───────────────────────────────────────────
    "adi":                                           "ADI",
    "automated driving interfaces":                  "ADI",
    "ft-adi":                                        "FT-ADI",
    "ara::adi":                                      "ara::adi",
    "ara adi":                                       "ara::adi",
    "sensor supplier interface":                      "Sensor Supplier Interface",
    "standardized sensor api":                       "Standardized Sensor API",
    "sensor fusion algorithm":                       "Sensor Fusion Algorithm",
    "sensor fusion integration":                     "Sensor Fusion Integration",
    "sensor implementation testing":                 "Sensor Implementation Testing",
    "sensor simulation":                             "Sensor Simulation",
    "fusion unit":                                   "Fusion Unit",
    "smart sensor":                                  "Smart Sensor",
    "car2x":                                         "Car2X",
    "car-to-x":                                      "Car2X",
    "uss":                                           "USS",
    "ultrasonic sensor":                             "USS",
    "open simulation interface":                     "OSI",
    "osi":                                           "OSI",
    "iso 23150":                                     "ISO 23150",
    "iso23150":                                      "ISO 23150",
    "iso 23150:2021":                                "ISO 23150",
    "capability vector":                             "Capability Vector",
    "service capability vector":                     "Capability Vector",
    "xil":                                           "XiL",
    "mil":                                           "MiL",
    "hil":                                           "HiL",
    "tracking service":                              "Tracking Service",
    "roadmark service":                              "Roadmark Service",
    "landmark service":                              "Landmark Service",
    "detection service":                             "Detection Service",
    "connection time":                               "Connection Time Configuration",
    "design time":                                   "Design Time Configuration",
    "aeb":                                           "AEB",
    "autonomous emergency braking":                  "AEB",

    # ── Crypto Services (CP EXP 602) ─────────────────────────────────────────────
    "csm":                                           "CSM",
    "crypto service manager":                        "CSM",
    "cryif":                                         "CRYIF",
    "crypto interface":                              "CRYIF",
    "crypto driver":                                 "CRYPTO",
    "crypto driver object":                          "Crypto Driver Object",
    "cdo":                                           "Crypto Driver Object",
    "crypto primitive":                              "Crypto Primitive",
    "key element":                                   "Key Element",
    "key type":                                      "Key Type",
    "key material":                                  "Key Material",
    "she":                                           "SHE",
    "security hardware extension":                   "SHE",
    "hsm":                                           "HSM",
    "hardware security module":                      "HSM",
    "mac":                                           "MAC",
    "message authentication code":                   "MAC",
    "csm_macgenerate":                               "Csm_MacGenerate",
    "cryif_processjob":                              "CryIf_ProcessJob",
    "crypto_processjob":                             "Crypto_ProcessJob",
    "crypto_processing_sync":                        "CRYPTO_PROCESSING_SYNC",
    "crypto_processing_async":                       "CRYPTO_PROCESSING_ASYNC",
    "crypto_operationmode_start":                    "CRYPTO_OPERATIONMODE_START",
    "crypto_operationmode_update":                   "CRYPTO_OPERATIONMODE_UPDATE",
    "crypto_operationmode_finish":                   "CRYPTO_OPERATIONMODE_FINISH",
    "crypto_operationmode_singlecall":               "CRYPTO_OPERATIONMODE_SINGLECALL",
    "crypto_ke_mac_key":                             "CRYPTO_KE_MAC_KEY",
    "csm_mainfunction":                              "Csm_MainFunction",
    "cdo_hash":                                      "CDO_HASH",
    "cdo_rng":                                       "CDO_RNG",
    "crypto_hw":                                     "CRYPTO_HW",
    "crypto_sw":                                     "CRYPTO_SW",

    "communication manager":                        "ComM",
    "comm":                                         "ComM",
    "can":                                          "Can",
    "controller area network":                      "Can",
    "nvm":                                          "NvM",
    "non-volatile memory":                          "NvM",
    "nonvolatile memory":                           "NvM",
    "dcm":                                          "Dcm",
    "diagnostic communication manager":             "Dcm",
    "dem":                                          "Dem",
    "diagnostic event manager":                     "Dem",
    "rte":                                          "RTE",
    "runtime environment":                          "RTE",
    "os":                                           "Os",
    "operating system":                             "Os",
    "com":                                          "Com",
    "bsw":                                          "BSW",
    "basic software":                               "BSW",
    "mcal":                                         "MCAL",

    # -------------------------------------------------------------------------
    # Standard name variants (original)
    # -------------------------------------------------------------------------
    "iso26262":                                     "ISO 26262",
    "iso 26262:2018":                               "ISO 26262",
    "iso 26262:2011":                               "ISO 26262",
    "iec 61508":                                    "IEC 61508",

    # -------------------------------------------------------------------------
    # Concept variants (original)
    # -------------------------------------------------------------------------
    "pdu":                                          "PDU",
    "protocol data unit":                           "PDU",
    "i-pdu":                                        "I-PDU",
    "ipdu":                                         "I-PDU",
    "i_pdu":                                        "I-PDU",
    "sdu":                                          "SDU",
    "service data unit":                            "SDU",
    "ecu":                                          "ECU",
    "electronic control unit":                      "ECU",

    # -------------------------------------------------------------------------
    # EXP – Explanation / Guideline documents
    # -------------------------------------------------------------------------
    "adaptive platform":                            "Adaptive Platform",
    "ap":                                           "Adaptive Platform",
    "adaptive autosar":                             "Adaptive Platform",
    "adaptive platform machine configuration":      "Adaptive Platform Machine Configuration",
    "machine configuration":                        "Adaptive Platform Machine Configuration",
    "ara com":                                      "ara::com API",
    "ara::com":                                     "ara::com API",
    "aracom":                                       "ara::com API",
    "ara com api":                                  "ara::com API",
    "bsw distribution":                             "BSW Distribution Guide",
    "basic software distribution":                  "BSW Distribution Guide",
    "cdd":                                          "CDD",
    "complex device driver":                        "CDD",
    "cdd design":                                   "CDD Design and Integration Guideline",
    "cdd integration":                              "CDD Design and Integration Guideline",
    "application level error handling":             "Application-Level Error Handling",
    "error handling":                               "Application-Level Error Handling",
    "error description":                            "Error Description",
    "functional safety measures":                   "Functional Safety Measures",
    "fusa":                                         "Functional Safety",
    "functional safety":                            "Functional Safety",
    "safety overview":                              "Safety Overview",
    "safety use case":                              "Safety Use Case",
    "interrupt handling":                           "Interrupt Handling Explanation",
    "isr":                                          "ISR",
    "interrupt service routine":                    "ISR",
    "ipsec":                                        "IPsec",
    "ip security":                                  "IPsec",
    "ipsec implementation":                         "IPsec Implementation Guidelines",
    "layered software architecture":                "Layered Software Architecture",
    "lsa":                                          "Layered Software Architecture",
    "macro encapsulation":                          "Macro Encapsulation of Interpolation Calls",
    "interpolation calls":                          "Macro Encapsulation of Interpolation Calls",
    "mode management":                              "Mode Management Guide",
    "modemanagement":                               "Mode Management Guide",
    "nv data handling":                             "NV Data Handling",
    "nvdata":                                       "NV Data Handling",
    "non-volatile data handling":                   "NV Data Handling",
    "parallel processing":                          "Parallel Processing Guidelines",
    "multicore":                                    "Parallel Processing Guidelines",
    "multi-core":                                   "Parallel Processing Guidelines",
    "platform design":                              "Platform Design",
    "sensor interfaces":                            "Sensor Interfaces",
    "someip":                                       "SOME/IP",
    "some/ip":                                      "SOME/IP",
    "some ip":                                      "SOME/IP",
    "scalable service-oriented middleware over ip":  "SOME/IP",
    "ai body and comfort":                          "AI Body and Comfort",
    "ai chassis":                                   "AI Chassis",
    "ai hmi multimedia and telematics":             "AI HMI Multimedia and Telematics",
    "ai occupant and pedestrian safety":            "AI Occupant and Pedestrian Safety",
    "ai powertrain":                                "AI Powertrain",
    "ai user guide":                                "AI User Guide",
    "crypto services":                              "Crypto Services",
    "cryptographic services":                       "Crypto Services",
    "utilization of crypto services":               "Utilization of Crypto Services",
    "vfb":                                          "VFB",
    "virtual functional bus":                       "VFB",

    # -------------------------------------------------------------------------
    # PRS – Protocol Specification documents
    # -------------------------------------------------------------------------
    "e2e protocol":                                 "E2E Protocol",
    "end to end protocol":                          "E2E Protocol",
    "end-to-end protocol":                          "E2E Protocol",
    "e2e":                                          "E2E",
    "end-to-end":                                   "E2E",
    "end to end":                                   "E2E",
    "log and trace protocol":                       "Log and Trace Protocol",
    "lat protocol":                                 "Log and Trace Protocol",
    "nm protocol":                                  "NM Protocol",
    "network management protocol":                  "NM Protocol",
    "autosar nm":                                   "NM Protocol",
    "someip protocol":                              "SOME/IP Protocol",
    "some/ip protocol":                             "SOME/IP Protocol",
    "someip sd":                                    "SOME/IP Service Discovery Protocol",
    "some/ip sd":                                   "SOME/IP Service Discovery Protocol",
    "someip service discovery":                     "SOME/IP Service Discovery Protocol",
    "some/ip service discovery":                    "SOME/IP Service Discovery Protocol",
    "testability protocol":                         "Testability Protocol and Service Primitives",
    "tap":                                          "Testability Protocol and Service Primitives",
    "time sync protocol":                           "Time Sync Protocol",
    "time synchronization protocol":                "Time Sync Protocol",
    "timesync protocol":                            "Time Sync Protocol",

    # -------------------------------------------------------------------------
    # RS – Requirement Specification documents
    # -------------------------------------------------------------------------
    "bsw module description template":              "BSW Module Description Template",
    "bswmdt":                                       "BSW Module Description Template",
    "communication management":                     "Communication Management",
    "cpp14 guidelines":                             "C++14 Guidelines",
    "cpp14":                                        "C++14",
    "c++14":                                        "C++14",
    "c++ 14":                                       "C++14",
    "c++14 guidelines":                             "C++14 Guidelines",
    "cryptography":                                 "Cryptography",
    "crypto":                                       "Cryptography",
    "diagnostic extract template":                  "Diagnostic Extract Template",
    "det":                                          "DET",
    "ecu configuration":                            "ECU Configuration",
    "ecuc":                                         "ECU Configuration",
    "ecu resource template":                        "ECU Resource Template",
    "ecurt":                                        "ECU Resource Template",
    "execution management":                         "Execution Management",
    "em":                                           "Execution Management",
    "execm":                                        "Execution Management",
    "feature model exchange format":                "Feature Model Exchange Format",
    "fmef":                                         "Feature Model Exchange Format",
    "autosar features":                             "AUTOSAR Features",
    "foundation debug trace profile":               "Foundation Debug Trace Profile",
    "fdtp":                                         "Foundation Debug Trace Profile",
    "autosar general":                              "General Requirements",
    "rs general":                                   "General Requirements",
    "health monitoring":                            "Health Monitoring",
    "phm":                                          "Platform Health Management",
    "platform health management":                   "Platform Health Management",
    "identity and access management":               "Identity and Access Management",
    "iam":                                          "Identity and Access Management",
    "interaction with behavioral models":           "Interaction with Behavioral Models",
    "behavioral models":                            "Behavioral Models",
    "interoperability of autosar tools":            "Interoperability of AUTOSAR Tools",
    "tool interoperability":                        "Interoperability of AUTOSAR Tools",
    "log and trace":                                "Log and Trace",
    "lat":                                          "Log and Trace",
    "logandtrace":                                  "Log and Trace",
    "rs main":                                      "RS Main",
    "autosar rs main":                              "RS Main",
    "manifest specification":                       "Manifest Specification",
    "manifest spec":                                "Manifest Specification",
    "arxml manifest":                               "Manifest Specification",
    "methodology and templates general":            "Methodology and Templates General",
    "methodology general":                          "Methodology and Templates General",
    "autosar methodology":                          "Methodology",
    "rs methodology":                               "Methodology",
    "network management":                           "Network Management",
    "nm":                                           "NM",
    "operating system interface":                   "Operating System Interface",
    "os interface":                                 "OS Interface",
    "persistency":                                  "Persistency",
    "per":                                          "Persistency",
    "project objectives":                           "Project Objectives",
    "autosar objectives":                           "Project Objectives",
    "autosar safety extensions":                    "Safety Extensions",
    "safety extensions":                            "Safety Extensions",
    "security management":                          "Security Management",
    "secm":                                         "Security Management",
    "software component template":                  "Software Component Template",
    "swct":                                         "SWC Template",
    "swc template":                                 "SWC Template",
    "standardization template":                     "Standardization Template",
    "state management":                             "State Management",
    "sm":                                           "State Management",
    "swc modeling":                                 "SWC Modeling",
    "software component modeling":                  "SWC Modeling",
    "system template":                              "System Template",
    "syst":                                         "System Template",
    "time synchronization":                         "Time Synchronization",
    "timesync":                                     "Time Synchronization",
    "time sync":                                    "Time Synchronization",
    "timing extensions":                            "Timing Extensions",
    "timex":                                        "Timing Extensions",
    "update and config management":                 "Update and Config Management",
    "ucm":                                          "UCM",
    "update configuration management":              "Update and Config Management",

    # -------------------------------------------------------------------------
    # SRS – Software Requirement Specification documents (BSW modules)
    # -------------------------------------------------------------------------
    "adc driver":                                   "ADC Driver",
    "adc":                                          "ADC Driver",
    "analog to digital converter driver":           "ADC Driver",
    "analog-to-digital converter driver":           "ADC Driver",
    "bsw general":                                  "BSW General",
    "basic software general":                       "BSW General",
    "bus mirroring":                                "Bus Mirroring",
    "can driver":                                   "CAN Driver",
    "canif":                                        "CanIf",
    "can interface":                                "CanIf",
    "core test":                                    "Core Test",
    "crypto stack":                                 "Crypto Stack",
    "cryptographic stack":                          "Crypto Stack",
    "diagnostics":                                  "Diagnostics",
    "diag":                                         "Diagnostics",
    "dio driver":                                   "DIO Driver",
    "dio":                                          "DIO Driver",
    "digital i/o driver":                           "DIO Driver",
    "digital io driver":                            "DIO Driver",
    "eeprom driver":                                "EEPROM Driver",
    "eeprom":                                       "EEPROM Driver",
    "electrically erasable programmable rom driver": "EEPROM Driver",
    "ethernet":                                     "Ethernet",
    "eth":                                          "Ethernet",
    "ethernet driver":                              "Ethernet Driver",
    "flash driver":                                 "Flash Driver",
    "fls":                                          "Flash Driver",
    "flash":                                        "Flash Driver",
    "flash test":                                   "Flash Test",
    "flexray":                                      "FlexRay",
    "fr":                                           "FlexRay",
    "flex ray":                                     "FlexRay",
    "free running timer":                           "Free Running Timer",
    "frt":                                          "Free Running Timer",
    "gpt":                                          "GPT Driver",
    "general purpose timer":                        "GPT Driver",
    "gpt driver":                                   "GPT Driver",
    "function inhibition manager":                  "Function Inhibition Manager",
    "fim":                                          "FiM",
    "gateway":                                      "Gateway",
    "com gateway":                                  "Gateway",
    "hw test manager":                              "HW Test Manager",
    "hardware test manager":                        "HW Test Manager",
    "htm":                                          "HW Test Manager",
    "icu driver":                                   "ICU Driver",
    "icu":                                          "ICU Driver",
    "input capture unit driver":                    "ICU Driver",
    "io hw abstraction":                            "IO HW Abstraction",
    "io hardware abstraction":                      "IO HW Abstraction",
    "iohwab":                                       "IO HW Abstraction",
    "i-pdu multiplexer":                            "I-PDU Multiplexer",
    "ipdu multiplexer":                             "I-PDU Multiplexer",
    "ipdumux":                                      "I-PDU Multiplexer",
    "pdu multiplexer":                              "I-PDU Multiplexer",
    "libraries":                                    "Libraries",
    "autosar libraries":                            "Libraries",
    "lin":                                          "LIN",
    "local interconnect network":                   "LIN",
    "lin driver":                                   "LIN Driver",
    "mcu driver":                                   "MCU Driver",
    "mcu":                                          "MCU Driver",
    "microcontroller unit driver":                  "MCU Driver",

    # -------------------------------------------------------------------------
    # ATS – Acceptance Test Specification documents
    # -------------------------------------------------------------------------
    "ats flexray":                                  "ATS FlexRay",
    "ats communication flexray":                    "ATS FlexRay Communication",
    "ats communication via bus":                    "ATS Communication Via Bus",
    "communication via bus":                        "Communication Via Bus",
    "flexray acceptance test":                      "ATS FlexRay",
    "bus communication acceptance test":            "ATS Communication Via Bus",
}

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 7 — EMBEDDING
# (uses EMBED_* settings above)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 8 — NEO4J STORAGE
# ══════════════════════════════════════════════════════════════════════════════

# kNN: for each chunk, create SIMILAR_TO edges to this many nearest neighbors
KNN_TOP_K = 10

# Minimum similarity score to create a SIMILAR_TO edge
KNN_MIN_SCORE = 0.82

# Neo4j write batch size (nodes/relationships per transaction)
NEO4J_BATCH_SIZE = 500

# ══════════════════════════════════════════════════════════════════════════════
# ASEI — AGENT LAYER SETTINGS
# All thresholds, limits, and provider configs for the agentic system.
# ══════════════════════════════════════════════════════════════════════════════

# ── Provider API keys (set via environment) ───────────────────────────────────
GROQ_API_KEY        = os.environ.get("GROQ_API_KEY", "")
SAMBANOVA_API_KEY   = os.environ.get("SAMBANOVA_API_KEY", "")
CEREBRAS_API_KEY    = os.environ.get("CEREBRAS_API_KEY", "")
OPENROUTER_API_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
BOSCH_API_KEY       = os.environ.get("GEMINI_API_KEY", "")   # shared key for Bosch endpoint
NVIDIA_API_KEY      = os.environ.get("NVIDIA_API_KEY", "")   # NVIDIA NIM endpoint

# ── Provider base URLs ────────────────────────────────────────────────────────
GROQ_BASE_URL       = "https://api.groq.com/openai/v1"
SAMBANOVA_BASE_URL  = "https://api.sambanova.ai/v1"
CEREBRAS_BASE_URL   = "https://api.cerebras.ai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
NVIDIA_BASE_URL     = "https://integrate.api.nvidia.com/v1"   # NVIDIA NIM endpoint
BOSCH_GPT4O_MINI_URL = (
    "https://aoai-farm.bosch-temp.com/api/openai/deployments/"
)

INTER_AGENT_DELAY_S: int = 60

NVIDIA_TIMEOUT_BY_MODEL: dict[str, int] = {
    "qwen/qwen3.5-397b-a17b":                  900,  # 397B — needs most time
    "deepseek-ai/deepseek-v4-pro":             900,
    "nvidia/nemotron-3-super-120b-a12b":       600,
    "qwen/qwen3.5-122b-a10b":                  600,  # 122B — needs time for CoT
    "mistralai/mistral-medium-3.5-128b":       600,  # reasoning_effort=high needs more time
    "google/gemma-4-31b-it":                   220,
    "qwen/qwen3-next-80b-a3b-instruct":        180,
    "meta/llama-3.3-70b-instruct":             180,
    "meta/llama-3.1-70b-instruct":             180,
    "mistralai/ministral-14b-instruct-2512":   180,
    "mistralai/mixtral-8x22b-instruct-v0.1":   180,
    "meta/llama-4-maverick-17b-128e-instruct": 120,
    "microsoft/phi-4-mini-instruct":            90,
    "nvidia/nemotron-mini-4b-instruct":         90,
    "google/gemma-3n-e4b-it":                   90,
    }

# ── NVIDIA NIM rate-limit & request tuning ────────────────────────────────────
NVIDIA_TIMEOUT_S         = int(os.environ.get("NVIDIA_TIMEOUT_S",    "600"))
NVIDIA_RETRIES           = int(os.environ.get("NVIDIA_RETRIES",       "0"))
NVIDIA_RPM_LIMIT         = int(os.environ.get("NVIDIA_RPM_LIMIT",     "3"))   # free tier = 5 RPM per model
NVIDIA_MAX_TOKENS        = int(os.environ.get("NVIDIA_MAX_TOKENS",  "1024"))

NVIDIA_MAX_TOKENS_BY_MODEL = {
    "qwen/qwen3.5-122b-a10b":                  16384,
    "qwen/qwen3.5-397b-a17b":                  16384,  # was 512
    "deepseek-ai/deepseek-v4-pro":             16384,  # was 768
    "nvidia/nemotron-3-super-120b-a12b":       16384,
    "mistralai/mistral-medium-3.5-128b":       16384,  # was 1024
    "qwen/qwen3-next-80b-a3b-instruct":         4096,  # was 1024
    "mistralai/ministral-14b-instruct-2512":    2048,
    "meta/llama-3.3-70b-instruct":              1024,  # correct
    "nvidia/nemotron-mini-4b-instruct":         1024,  # correct
    "meta/llama-3.1-70b-instruct":              1024,  # correct
    "microsoft/phi-4-mini-instruct":            1024,  # correct
    "mistralai/mixtral-8x22b-instruct-v0.1":    1024,  # correct
    "meta/llama-4-maverick-17b-128e-instruct":   512,  # was 1024 (you were over-limit)
    "google/gemma-3n-e4b-it":                    512,  # was 1024 (over-limit)
}

    # ── Model names per provider ──────────────────────────────────────────────────

GROQ_MODEL_HEAVY    = "openai/gpt-oss-120b"       # reasoning, conflict, verification
GROQ_MODEL_MID      = "qwen/qwen3-32b"             # structured extraction, debate leg
GROQ_MODEL_FAST     = "llama-3.3-70b-versatile"   # impact, fast debate leg
SAMBANOVA_MODEL_PRIMARY   = "DeepSeek-V3.2"        # synthesis, hypothesis
SAMBANOVA_MODEL_FALLBACK  = "DeepSeek-V3.1"        # fallback for primary
SAMBANOVA_MODEL_MID       = "Meta-Llama-3.3-70B-Instruct"
CEREBRAS_MODEL      = "llama3.1-8b"                # router, query memory (fastest)
OPENROUTER_MODEL_CODER  = "qwen/qwen3-coder:free"  # gap detection (262K ctx, formal specs)
OPENROUTER_MODEL_LONG   = "google/gemma-4-31b-it:free"   # long summarization fallback
OPENROUTER_MODEL_TINY   = "meta-llama/llama-3.2-3b-instruct:free"  # watchdog / cheap classify
BOSCH_MODEL         = "gpt-4o-mini"               # Bosch endpoint model alias

# ── NVIDIA NIM model assignments (by agent role) ──────────────────────────────

NVIDIA_MODEL_PROSECUTOR    = "qwen/qwen3.5-397b-a17b"
NVIDIA_MODEL_DEFENDER      = "deepseek-ai/deepseek-v4-pro"
NVIDIA_MODEL_SKEPTIC       = "meta/llama-3.3-70b-instruct"

NVIDIA_MODEL_SYNTHESIS     = "qwen/qwen3.5-122b-a10b"
NVIDIA_MODEL_CONFLICT      = "meta/llama-4-maverick-17b-128e-instruct"
NVIDIA_MODEL_VERIFICATION  = "meta/llama-3.1-70b-instruct"

NVIDIA_MODEL_GAP_SUPER     = "nvidia/nemotron-3-super-120b-a12b"
NVIDIA_MODEL_GAP_PRIMARY   = "mistralai/ministral-14b-instruct-2512"
NVIDIA_MODEL_GAP_FALLBACK  = "microsoft/phi-4-mini-instruct"

NVIDIA_MODEL_SUMMARIZATION = "mistralai/mistral-medium-3.5-128b"
NVIDIA_MODEL_IMPACT        = "qwen/qwen3-next-80b-a3b-instruct"
NVIDIA_MODEL_IMPACT_FB     = "mistralai/mixtral-8x22b-instruct-v0.1"

NVIDIA_MODEL_CLASSIFY_FB   = "google/gemma-3n-e4b-it"
NVIDIA_MODEL_SYNTH_FB      = "nvidia/nemotron-mini-4b-instruct"

# ── Orchestrator ──────────────────────────────────────────────────────────────
ASEI_CYCLE_INTERVAL_S   = int(os.environ.get("ASEI_CYCLE_INTERVAL_S", "43200 "))  # 12 hour
ASEI_STATE_DIR          = os.environ.get("ASEI_STATE_DIR", "./output/asei_state")

# ── Evolution Agent ───────────────────────────────────────────────────────────
ASEI_STALENESS_DAYS             = int(os.environ.get("ASEI_STALENESS_DAYS", "30"))
ASEI_LOW_CONFIDENCE_THRESHOLD   = float(os.environ.get("ASEI_LOW_CONFIDENCE_THRESHOLD", "0.70"))

# ── Conflict Agent ────────────────────────────────────────────────────────────
ASEI_CONFLICT_STRUCT_LIMIT          = int(os.environ.get("ASEI_CONFLICT_STRUCT_LIMIT", "200"))
ASEI_CONFLICT_SEMANTIC_LIMIT        = int(os.environ.get("ASEI_CONFLICT_SEMANTIC_LIMIT", "30"))
ASEI_CONFLICT_SIMILARITY_THRESHOLD  = float(os.environ.get("ASEI_CONFLICT_SIMILARITY_THRESHOLD", "0.92"))

# ── Synthesis Agent ───────────────────────────────────────────────────────────
ASEI_SYNTHESIS_CANDIDATE_LIMIT  = int(os.environ.get("ASEI_SYNTHESIS_CANDIDATE_LIMIT", "500"))
ASEI_SYNTHESIS_LLM_LIMIT        = int(os.environ.get("ASEI_SYNTHESIS_LLM_LIMIT", "40"))
ASEI_SYNTHESIS_MIN_BRIDGE_COUNT = int(os.environ.get("ASEI_SYNTHESIS_MIN_BRIDGE_COUNT", "2"))
ASEI_SYNTHESIS_MIN_CONFIDENCE   = float(os.environ.get("ASEI_SYNTHESIS_MIN_CONFIDENCE", "0.70"))

# ── Verification Agent ────────────────────────────────────────────────────────
ASEI_VERIFICATION_BATCH         = int(os.environ.get("ASEI_VERIFICATION_BATCH", "20"))
ASEI_VERIFICATION_REJECT_THRESHOLD = float(os.environ.get("ASEI_VERIFICATION_REJECT_THRESHOLD", "0.40"))

# ── Reasoning Agent ───────────────────────────────────────────────────────────
ASEI_REASONING_TOP_K            = int(os.environ.get("ASEI_REASONING_TOP_K", "5"))
ASEI_REASONING_MAX_HOPS         = int(os.environ.get("ASEI_REASONING_MAX_HOPS", "5"))
ASEI_REASONING_MIN_SIMILARITY   = float(os.environ.get("ASEI_REASONING_MIN_SIMILARITY", "0.45"))
ASEI_REASONING_CONTEXT_TOKENS   = int(os.environ.get("ASEI_REASONING_CONTEXT_TOKENS", "8000"))
ASEI_REASONING_DEBATE_WEIGHT_HEAVY  = float(os.environ.get("ASEI_REASONING_DEBATE_WEIGHT_HEAVY", "0.45"))
ASEI_REASONING_DEBATE_WEIGHT_MID    = float(os.environ.get("ASEI_REASONING_DEBATE_WEIGHT_MID", "0.35"))
ASEI_REASONING_DEBATE_WEIGHT_LOCAL  = float(os.environ.get("ASEI_REASONING_DEBATE_WEIGHT_LOCAL", "0.20"))

# ── Summarization Agent ───────────────────────────────────────────────────────
ASEI_SUMMARY_MAX_CHUNKS_PER_MODULE  = int(os.environ.get("ASEI_SUMMARY_MAX_CHUNKS_PER_MODULE", "30"))
ASEI_SUMMARY_CONTEXT_CHARS          = int(os.environ.get("ASEI_SUMMARY_CONTEXT_CHARS", "8000"))

# ── Gap Detection Agent ───────────────────────────────────────────────────────
ASEI_GAP_CANDIDATE_LIMIT        = int(os.environ.get("ASEI_GAP_CANDIDATE_LIMIT", "50"))
ASEI_GAP_MIN_CONFIDENCE         = float(os.environ.get("ASEI_GAP_MIN_CONFIDENCE", "0.65"))

# ── Impact Agent ──────────────────────────────────────────────────────────────
ASEI_IMPACT_MAX_HOPS            = int(os.environ.get("ASEI_IMPACT_MAX_HOPS", "5"))
ASEI_IMPACT_BATCH               = int(os.environ.get("ASEI_IMPACT_BATCH", "50"))

# ── Watchdog Agent ────────────────────────────────────────────────────────────
ASEI_WATCHDOG_REJECTION_CEILING     = float(os.environ.get("ASEI_WATCHDOG_REJECTION_CEILING", "0.40"))
ASEI_WATCHDOG_ERROR_CEILING         = float(os.environ.get("ASEI_WATCHDOG_ERROR_CEILING", "0.20"))

# ── Query Memory Agent ────────────────────────────────────────────────────────
ASEI_QUERY_MEMORY_LOW_CONF_THRESHOLD    = float(os.environ.get("ASEI_QUERY_MEMORY_LOW_CONF_THRESHOLD", "0.60"))
ASEI_QUERY_MEMORY_HOT_SPOT_COUNT        = int(os.environ.get("ASEI_QUERY_MEMORY_HOT_SPOT_COUNT", "3"))