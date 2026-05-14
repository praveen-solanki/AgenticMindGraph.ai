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
LLM_MAX_TOKENS         = 4096              # enough for entity extraction JSON
LLM_TIMEOUT            = 300               # seconds per request
LLM_MAX_CONCURRENT     = 8              # match --max-num-seqs 16

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
PDF_HEADER_MARGIN = 0.12   # 12% top  — AUTOSAR docs have large headers
PDF_FOOTER_MARGIN = 0.12   # 10% bottom — page numbers + "AUTOSAR confidential"

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — NOISE REMOVAL
# ══════════════════════════════════════════════════════════════════════════════

# A line appearing on this fraction of pages or more = running header/footer
REPEATED_LINE_THRESHOLD   = 0.40

# If this fraction of lines on a page match TOC pattern = TOC page → drop
TOC_LINE_RATIO_THRESHOLD  = 0.50

# If this fraction of lines contain date/version patterns = revision page
REVISION_LINE_RATIO       = 0.40

# Pages with fewer than this many chars after cleaning = near-blank → drop
MIN_PAGE_CHARS            = 150

# Lines shorter than this starting with Figure/Table/etc = orphaned caption
CAPTION_MAX_LEN           = 40

# Cross-document boilerplate: cosine similarity above this = same boilerplate
BOILERPLATE_SIM_THRESHOLD = 0.95

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — REQUIREMENT ID HARVESTING
# ══════════════════════════════════════════════════════════════════════════════

# Max IDs per page before skipping cross-ref pair generation (index page guard)
MAX_IDS_PER_PAGE_FOR_XREF = 15

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
]
# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — CHUNKING
# ══════════════════════════════════════════════════════════════════════════════

CHUNK_MAX_TOKENS      = 756    # Max tokens per chunk
CHUNK_OVERLAP_TOKENS  = 128     # Overlap when splitting oversized chunks
CHUNK_MIN_TOKENS      = 40     # Drop chunks smaller than this
CHUNK_TABLE_MAX_TOKENS= 1200    # Tables may exceed normal max — kept whole
MIN_UNIQUE_WORD_RATIO = 0.30   # Drop chunks with low lexical diversity

# Heading levels to split on

SPLIT_HEADERS = [
    ("#",     "H1"),
    ("##",    "H2"),
    ("###",   "H3"),
    ("####",  "H4"),
    ("#####", "H5"),
]

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — ENTITY & RELATION EXTRACTION SCHEMA
# ══════════════════════════════════════════════════════════════════════════════
# Customize these to match your specific AUTOSAR document corpus.
# Read 20-30 pages of your actual docs first, then adjust.

ALLOWED_NODES = [
    # --- Your originals (unchanged) ---
    "Requirement",        # [SWS_X_NNNNN], [SRS_X_NNNNN], [TR_X_NNNNN], [AP_TPS_*] etc.
    "Document",   # First-class AUTOSAR PDF/document entity
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
KNN_MIN_SCORE = 0.80

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


NVIDIA_TIMEOUT_BY_MODEL: dict[str, int] = {
    "qwen/qwen3.5-397b-a17b":                  300,  # 397B — needs most time
    "deepseek-ai/deepseek-v4-pro":             180,
    "qwen/qwen3-next-80b-a3b-thinking":        300,  # thinking model — extra time for CoT
    "qwen/qwen3-next-80b-a3b-instruct":        120,
    "meta/llama-3.3-70b-instruct":             120,
    "meta/llama-3.1-70b-instruct":             120,
    "mistralai/mistral-medium-3.5-128b":       300,  # reasoning_effort=high needs more time
    "google/gemma-4-31b-it":                   120,
    "mistralai/ministral-14b-instruct-2512":   120,
    "mistralai/mixtral-8x22b-instruct-v0.1":    90,
    "meta/llama-4-maverick-17b-128e-instruct":  90,
    "microsoft/phi-4-mini-instruct":            60,
    "nvidia/nemotron-mini-4b-instruct":         60,
    "google/gemma-3n-e4b-it":                   60,
}

# ── NVIDIA NIM rate-limit & request tuning ────────────────────────────────────
NVIDIA_TIMEOUT_S         = int(os.environ.get("NVIDIA_TIMEOUT_S",    "400"))
NVIDIA_RETRIES           = int(os.environ.get("NVIDIA_RETRIES",       "0"))
NVIDIA_RPM_LIMIT         = int(os.environ.get("NVIDIA_RPM_LIMIT",     "3"))   # free tier = 5 RPM per model
NVIDIA_MAX_TOKENS        = int(os.environ.get("NVIDIA_MAX_TOKENS",  "1024"))

# Per-model output token caps (NIM free tier restrictions)
# NVIDIA_MAX_TOKENS_BY_MODEL: dict[str, int] = {
#     "qwen/qwen3.5-397b-a17b":                    512,   # huge model, slow, cap output aggressively
#     "deepseek-ai/deepseek-v4-pro":               768,
#     "meta/llama-3.3-70b-instruct":              1024,
#     "qwen/qwen3-next-80b-a3b-thinking":          768,
#     "nvidia/nemotron-mini-4b-instruct":         1024,
#     "meta/llama-4-maverick-17b-128e-instruct":  1024,
#     "meta/llama-3.1-70b-instruct":             1024,
#     "google/gemma-4-31b-it":                   1024,
#     "microsoft/phi-4-mini-instruct":            1024,
#     "mistralai/mistral-medium-3.5-128b":        1024,
#     "qwen/qwen3-next-80b-a3b-instruct":         1024,
#     "mistralai/mixtral-8x22b-instruct-v0.1":   1024,
#     "google/gemma-3n-e4b-it":                  1024,
# }


NVIDIA_MAX_TOKENS_BY_MODEL = {
    "qwen/qwen3.5-397b-a17b":                  16384,  # was 512
    "deepseek-ai/deepseek-v4-pro":             16384,  # was 768
    "meta/llama-3.3-70b-instruct":              1024,  # correct
    "qwen/qwen3-next-80b-a3b-thinking":         8192,  # was 768
    "qwen/qwen3-next-80b-a3b-instruct":         4096,  # was 1024
    "nvidia/nemotron-mini-4b-instruct":         1024,  # correct
    "meta/llama-4-maverick-17b-128e-instruct":   512,  # was 1024 (you were over-limit)
    "meta/llama-3.1-70b-instruct":             1024,  # correct
    # "google/gemma-4-31b-it":                   16384,
    "mistralai/ministral-14b-instruct-2512":    2048,
    "microsoft/phi-4-mini-instruct":            1024,  # correct
    "mistralai/mistral-medium-3.5-128b":       16384,  # was 1024
    "qwen/qwen3-next-80b-a3b-instruct":         4096,  # was 1024
    "mistralai/mixtral-8x22b-instruct-v0.1":   1024,  # correct
    "google/gemma-3n-e4b-it":                   512,  # was 1024 (over-limit)
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

NVIDIA_MODEL_SYNTHESIS     = "qwen/qwen3-next-80b-a3b-thinking"
NVIDIA_MODEL_CONFLICT      = "meta/llama-4-maverick-17b-128e-instruct"
NVIDIA_MODEL_VERIFICATION  = "meta/llama-3.1-70b-instruct"

NVIDIA_MODEL_GAP_PRIMARY   = "mistralai/ministral-14b-instruct-2512"
NVIDIA_MODEL_GAP_FALLBACK  = "microsoft/phi-4-mini-instruct"

NVIDIA_MODEL_SUMMARIZATION = "mistralai/mistral-medium-3.5-128b"
NVIDIA_MODEL_IMPACT        = "qwen/qwen3-next-80b-a3b-instruct"
NVIDIA_MODEL_IMPACT_FB     = "mistralai/mixtral-8x22b-instruct-v0.1"

NVIDIA_MODEL_CLASSIFY_FB   = "google/gemma-3n-e4b-it"
NVIDIA_MODEL_SYNTH_FB      = "nvidia/nemotron-mini-4b-instruct"

# ── Orchestrator ──────────────────────────────────────────────────────────────
ASEI_CYCLE_INTERVAL_S   = int(os.environ.get("ASEI_CYCLE_INTERVAL_S", "3600"))  # 1 hour
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
ASEI_REASONING_MAX_HOPS         = int(os.environ.get("ASEI_REASONING_MAX_HOPS", "3"))
ASEI_REASONING_MIN_SIMILARITY   = float(os.environ.get("ASEI_REASONING_MIN_SIMILARITY", "0.70"))
ASEI_REASONING_CONTEXT_TOKENS   = int(os.environ.get("ASEI_REASONING_CONTEXT_TOKENS", "6000"))
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
ASEI_IMPACT_MAX_HOPS            = int(os.environ.get("ASEI_IMPACT_MAX_HOPS", "4"))
ASEI_IMPACT_BATCH               = int(os.environ.get("ASEI_IMPACT_BATCH", "50"))

# ── Watchdog Agent ────────────────────────────────────────────────────────────
ASEI_WATCHDOG_REJECTION_CEILING     = float(os.environ.get("ASEI_WATCHDOG_REJECTION_CEILING", "0.40"))
ASEI_WATCHDOG_ERROR_CEILING         = float(os.environ.get("ASEI_WATCHDOG_ERROR_CEILING", "0.20"))

# ── Query Memory Agent ────────────────────────────────────────────────────────
ASEI_QUERY_MEMORY_LOW_CONF_THRESHOLD    = float(os.environ.get("ASEI_QUERY_MEMORY_LOW_CONF_THRESHOLD", "0.60"))
ASEI_QUERY_MEMORY_HOT_SPOT_COUNT        = int(os.environ.get("ASEI_QUERY_MEMORY_HOT_SPOT_COUNT", "3"))
