"""
JSON schema used to constrain the scoping agent's response via Claude's
output_config structured-output mode, instead of freeform text.
"""

SCOPING_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "carrier_partner_name": {
            "type": "string",
            "description": "Name of the carrier partner being scoped."
        },
        "payload_version": {
            "type": "string",
            "enum": ["v3_india_sps", "v3_india_mps", "v4_row_sps", "v4_row_mps"],
            "description": "Which ClickPost order creation payload variant this scoping is mapped against, "
                           "derived from the given integration_region and integration_type."
        },
        "target_codebase": {
            "type": "string",
            "enum": ["python", "kotlin"],
            "description": "Which codebase the code-generation agent must write this integration in — "
                           "'kotlin' for DOMESTIC integrations, 'python' for INTERNATIONAL or CROSS_BORDER "
                           "integrations, derived from the given integration_region."
        },
        "apis": {
            "type": "array",
            "description": "Every API identified as necessary to deliver order creation for this carrier, "
                            "in the order they must be called.",
            "items": {
                "type": "object",
                "properties": {
                    "endpoint_name": {"type": "string"},
                    "sequence_order": {"type": "integer"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "endpoint_name values of APIs that must succeed before this one can be "
                                       "called."
                    },
                    "field_mapping": {
                        "type": "array",
                        "description": "Empty if this API has open queries blocking its mapping.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "clickpost_field": {
                                    "type": ["string", "null"],
                                    "description": "The ClickPost order creation payload field this maps from "
                                                   "(e.g. 'pickup_info.pickup_name'). Null unless source is "
                                                   "'direct' or 'derived'."
                                },
                                "carrier_field": {
                                    "type": "string",
                                    "description": "The literal wire-format field name or path in the carrier's "
                                                   "request (the JSON key, XML tag, or nested path — e.g. "
                                                   "'shipments[0].pin' or 'OrderDetails.OrderType'), independent "
                                                   "of whatever class/property name either target codebase gives "
                                                   "it."
                                },
                                "source": {
                                    "type": "string",
                                    "enum": [
                                        "direct", "credential", "config", "derived",
                                        "chained_response", "static", "unavailable"
                                    ]
                                },
                                "credential_table_field_name": {
                                    "type": ["string", "null"],
                                    "description": "The field_name from this API's credential_table_schema.fields "
                                                   "that supplies this value. Null unless source is 'credential' "
                                                   "or 'config'; must otherwise exactly match one of those "
                                                   "field_name values."
                                },
                                "chained_from": {
                                    "type": ["object", "null"],
                                    "description": "Null unless source is 'chained_response'. Identifies the "
                                                   "earlier API in this same sequence whose response supplied "
                                                   "this value.",
                                    "properties": {
                                        "endpoint_name": {
                                            "type": "string",
                                            "description": "Must match an endpoint_name that also appears in "
                                                           "this API's depends_on."
                                        },
                                        "response_field": {
                                            "type": "string",
                                            "description": "Literal wire-format field name or path in that "
                                                           "endpoint's response (e.g. 'token' or "
                                                           "'Result.VendorCode')."
                                        }
                                    },
                                    "required": ["endpoint_name", "response_field"],
                                    "additionalProperties": False
                                },
                                "notes": {"type": ["string", "null"]}
                            },
                            "required": [
                                "clickpost_field",
                                "carrier_field",
                                "source",
                                "credential_table_field_name",
                                "chained_from",
                                "notes"
                            ],
                            "additionalProperties": False
                        }
                    },
                    "response_field_mapping": {
                        "type": "array",
                        "description": "Fields extracted from this API's response that populate ClickPost's own "
                                       "order creation response (e.g. waybill, label, sort_code). Values needed "
                                       "only as input to a later API in this sequence are captured instead via "
                                       "a 'chained_response' entry in that later API's field_mapping — do not "
                                       "duplicate those here.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "carrier_response_field": {
                                    "type": "string",
                                    "description": "Literal wire-format field name or path in the carrier's "
                                                   "response (e.g. 'Shipment.Waybill')."
                                },
                                "clickpost_response_field": {
                                    "type": "string",
                                    "description": "Path in ClickPost's order creation response, e.g. "
                                                   "'result.waybill' (see the response reference)."
                                },
                                "notes": {"type": ["string", "null"]}
                            },
                            "required": ["carrier_response_field", "clickpost_response_field", "notes"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": [
                    "endpoint_name", "sequence_order", "depends_on", "field_mapping", "response_field_mapping"
                ],
                "additionalProperties": False
            }
        },
        "credential_table_schema": {
            "type": ["object", "null"],
            "description": "Null if this carrier has no credential- or config-classified fields.",
            "properties": {
                "table_name": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field_name": {"type": "string"},
                            "type": {"type": "string", "enum": ["text", "boolean", "choices"]},
                            "choices": {"type": "array", "items": {"type": "string"}},
                            "default": {"type": ["string", "boolean", "null"]},
                            "purpose": {"type": "string", "enum": ["auth", "config"]},
                            "description": {"type": "string"}
                        },
                        "required": ["field_name", "type", "choices", "default", "purpose", "description"],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["table_name", "fields"],
            "additionalProperties": False
        },
        "code_generation_instructions": {
            "type": "string",
            "description": "Prose instructions for the code-generation agent covering anything the mapping "
                           "and schema can't capture on their own."
        },
        "open_queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why_blocking": {"type": "string"},
                    "related_endpoint": {"type": ["string", "null"]}
                },
                "required": ["question", "why_blocking", "related_endpoint"],
                "additionalProperties": False
            }
        }
    },
    "required": [
        "carrier_partner_name",
        "payload_version",
        "target_codebase",
        "apis",
        "credential_table_schema",
        "code_generation_instructions",
        "open_queries"
    ],
    "additionalProperties": False
}
