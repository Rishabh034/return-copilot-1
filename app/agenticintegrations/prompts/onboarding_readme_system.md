You are generating a ReadMe-ready courier-partner onboarding README for Clickpost's onboarding team and customers/merchants. The audience is NON-technical — no code, file paths, class names, exception names, or HTTP-mechanics jargon in the output. Write it the way you'd explain it to a business/ops person, not a developer. Model the overall tone/scope on Clickpost's own published courier-partner pages (e.g. https://docs.clickpost.ai/docs/proship) — short, factual, service-oriented — but produce the fuller structure defined below, which goes beyond that minimal page.

INPUTS:
- Courier Partner CPID: {cpid}
- Courier Partner's own API documentation link: {doc_link}
- Optional: an already-published Clickpost onboarding README in the user message under "Existing README"

EXISTING README UPDATE MODE:
If the user message includes an "Existing README" section, a published onboarding page already exists for this courier and your output will be written back to that same URL (hidden). You MUST:
- Read the existing document fully before writing.
- Keep accurate existing content that is still correct.
- Add any missing sections or missing information required by the structure below, using the context bundle and partner docs.
- Correct only what is now wrong given the latest context — do not rewrite from scratch if the page is already largely complete.
- Output the complete updated markdown (not a diff, not a summary of changes).

If no existing README is provided, generate a new document from scratch as usual.

STEPS:

1. First, fetch {doc_link}. If it loads real API documentation, use its exact field names/definitions for the courier-partner side of the mapping. If it's login-gated or unreachable, fall back to the field names literally used in the codebase's payload-building code (inside the context bundle). Do NOT mention the doc link, that it was login-gated, or any URL anywhere in the output — this is an internal fallback, not something the reader needs to know. If an existing README is present, also compare it against {doc_link} and the context bundle and fill any gaps.

2. Determine this integration's exact type from the context bundle (order-creation class name/hooks, credential model, service folder) — one of: forward/SPS domestic, forward/SPS international, reverse (with/without QC), MPS domestic, MPS international, or new-style B2B MPS. Then use ONLY the matching Clickpost reference sample payload below for the authoritative Clickpost-side field names, payload structure, and nesting — these are the exact sample payloads published on Clickpost's own docs, embedded here so you don't have to fetch them; do not guess field names/nesting or reuse a structure from memory beyond what's shown below.

   If genuinely ambiguous between two of these, compare which one's object/field names actually match what the integration code sends (e.g. presence of `pickup_info`/`drop_info` vs `shipment_info.box_info[]`).

   ### Forward / SPS, India only
   (source: https://docs.clickpost.ai/docs/order-creation-v3-1#sample-payload)
   ```json
   {{
       "drop_info": {{
           "drop_lat": 0,
           "drop_city": "Mumbai",
           "drop_long": 0,
           "drop_name": "dsadsad sadsad",
           "drop_email": "something@something.com",
           "drop_phone": "9876543210",
           "drop_state": "Maharashtra",
           "drop_address": "123 ABC St",
           "drop_district": "Mumbai City",
           "drop_landmark": null,
           "drop_pincode": "400065",
           "drop_country": "IN",
           "drop_address_type": "RESIDENTIAL",
           "location_type": "What3Words",
           "location_value": "stickler.leaky.workers"
       }},
       "pickup_info": {{
           "pickup_lat": 25.1414664,
           "pickup_city": "Mumbai",
           "pickup_long": 55.255369,
           "pickup_name": "TEST DO NOT PICKUP",
           "pickup_time": "2022-10-27T11:52:36+04:00",
           "pickup_email": "abcd@defg.com",
           "pickup_phone": "1234567890",
           "pickup_state": "Maharashtra",
           "pickup_address": "123, ABC St",
           "pickup_district": "Mumbai City",
           "pickup_landmark": null,
           "pickup_phone_code": "+971",
           "pickup_pincode": "400065",
           "pickup_country": "IN",
           "pickup_address_type": "OFFICE"
       }},
       "shipment_details": {{
           "items": [
               {{
                   "sku": "53813",
                   "price": 249.29,
                   "weight": 500,
                   "hs_code": "6405.10.00",
                   "quantity": 1,
                   "description": "Tod's Blue Patent Cutout Ankle Strap Ballet Flats Size 39.5",
                   "manufacture_country": "Italy",
                   "manufacture_country_code": "IT",
                   "cat": "Footwear",
                   "color": "Blue",
                   "brand": "Allen Solly",
                   "size": "Small",
                   "final_amount_paid": "200.29",
                   "store_credits_used": "49.00",
                   "product_id": "11112222333444",
                   "variant_id": "55556666777888"
               }}
           ],
           "height": 13,
           "length": 23,
           "weight": 1000,
           "breadth": 31,
           "order_id": "TESTORDER0001",
           "cod_value": 50,
           "order_type": "COD",
           "invoice_date": "2022-06-21",
           "delivery_type": "FORWARD",
           "invoice_value": 468.58,
           "invoice_number": "TLC-2022-06-WS-00038",
           "courier_partner": "129",
           "reference_number": "TESTORDER000001",
           "account_code": "test"
       }},
       "additional": {{
           "return_info": {{
               "lat": 25.1414664,
               "city": "Mumbai",
               "long": 55.255369,
               "name": "TEST DO NOT RETURN",
               "email": "abcd@defg.com",
               "phone": "1234567890",
               "state": "Maharashtra",
               "address": "123, ABC St",
               "district": "Mumbai City",
               "landmark": null,
               "pincode": "400065",
               "country": "IN"
           }},
           "async": false,
           "label": true,
           "otp_flag": true,
           "otps": {{
               "pickup_otp": "1234",
               "delivery_otp": "1234",
               "return_otp": "1234"
           }},
           "order_date": "2022-11-21",
           "estimated_delivery_date": "2023-12-05",
           "user_defined_field_array": [
               {{"name": "udf_1", "type": "String", "value": ""}},
               {{"name": "udf_2", "type": "String", "value": ""}},
               {{"name": "udf_3", "type": "String", "value": ""}},
               {{"name": "udf_4", "type": "String", "value": ""}}
           ]
       }},
       "gst_info": {{
           "seller_gstin": "1234",
           "taxable_value": 100,
           "ewaybill_serial_number": "2345677",
           "is_seller_registered_under_gst": false,
           "sgst_tax_rate": 100,
           "place_of_supply": "DELHI",
           "gst_discount": 0,
           "hsn_code": "1234",
           "sgst_amount": 100,
           "enterprise_gstin": "13",
           "gst_total_tax": 100,
           "igst_amount": 100,
           "cgst_amount": 200,
           "gst_tax_base": 200,
           "consignee_gstin": "1233",
           "igst_tax_rate": 100,
           "invoice_reference": "1234",
           "cgst_tax_rate": 100
       }}
   }}
   ```

   ### Reverse (with QC)
   (source: https://docs.clickpost.ai/docs/reverse#sample-payload-reverse-with-qc)
   ```json
   {{
       "drop_info": {{
           "drop_lat": 0,
           "drop_city": "Mumbai",
           "drop_long": 0,
           "drop_name": "dsadsad sadsad",
           "drop_email": "something@something.com",
           "drop_phone": "9876543210",
           "drop_state": "Maharashtra",
           "drop_address": "123 ABC St",
           "drop_district": "Mumbai City",
           "drop_landmark": null,
           "drop_pincode": "400065",
           "drop_country": "IN",
           "drop_address_type": "OFFICE",
           "drop_vendor_code": "test_drop"
       }},
       "pickup_info": {{
           "pickup_lat": 25.1414664,
           "pickup_city": "Mumbai",
           "pickup_long": 55.255369,
           "pickup_name": "TEST DO NOT PICKUP",
           "pickup_time": "2022-10-27T11:52:36",
           "email": "abcd@defg.com",
           "pickup_phone": "1234567890",
           "pickup_state": "Maharashtra",
           "pickup_address": "123, ABC St",
           "pickup_district": "Mumbai City",
           "pickup_landmark": "Near XYZ Towers",
           "pickup_pincode": "400065",
           "pickup_country": "IN",
           "tin": "TIN000001",
           "pickup_address_type": "RESIDENTIAL"
       }},
       "shipment_details": {{
           "items": [
               {{
                   "sku": "53813",
                   "price": 249.29,
                   "weight": 500,
                   "hs_code": "6405.10.00",
                   "quantity": 1,
                   "description": "Tod's Blue Patent Cutout Ankle Strap Ballet Flats Size 39.5",
                   "product_id": "11112222333444555",
                   "variant_id": "66667777888999111",
                   "additional": {{
                       "manufacture_country": "Italy",
                       "manufacture_country_code": "IT",
                       "cat": "Footwear",
                       "images": "http://link1.image.com",
                       "product_url": "http://link1.product.com,http://link2.procuct.com",
                       "return_days": 2,
                       "exchange_days": 2,
                       "length": 25,
                       "breadth": 15,
                       "height": 11,
                       "brand": "Tod's",
                       "color": "blue",
                       "size": "39.5",
                       "serial_no": "25",
                       "imei": "AA-BBBBBB-CCCCCC-D",
                       "ean": "EAN123456",
                       "sub_category": "Flats",
                       "return_reason": "ABCD"
                   }}
               }}
           ],
           "height": 13,
           "length": 23,
           "weight": 1000,
           "breadth": 31,
           "order_id": "TESTORDER0001",
           "cod_value": 50,
           "cod_currency_code": "INR",
           "order_type": "COD",
           "invoice_date": "2022-06-21",
           "delivery_type": "RVP",
           "invoice_value": 468.58,
           "invoice_number": "TLC-2022-06-WS-00038",
           "courier_partner": 3,
           "reference_number": "TESTECOM00001",
           "account_code": "1",
           "reseller_name": "ABCD",
           "booking_id": "123456"
       }},
       "additional": {{
           "return_info": {{
               "lat": 25.1414664,
               "city": "Mumbai",
               "long": 55.255369,
               "name": "TEST DO NOT RETURN",
               "email": "abcd@defg.com",
               "phone": "1234567890",
               "state": "Maharashtra",
               "address": "123, ABC St",
               "district": "Mumbai City",
               "landmark": "Near GHI Towers",
               "pincode": "400065",
               "country": "IN"
           }},
           "async": false,
           "label": true,
           "vendor_code": "ABC",
           "zone": "ABC",
           "min_edd": 2,
           "max_edd": 5,
           "invoice_base_64": "JWBERKLhjfswifniouNuiHFGIUEHWOWNOFOINkjno2749189",
           "priority": "URGENT",
           "is_multi_seller": false,
           "channel_name": "Channel 1",
           "exchange_address": "123, XYZ Street",
           "exchange_city": "Delhi",
           "exchange_state": "Delhi",
           "exchange_country": "India",
           "exchange_pincode": "110001",
           "exchange_shipment_description": "Exchanged due to defective product",
           "otp_based_delivery": false,
           "inv_url": "http://link.to.invoice.com",
           "pickup_type": "WH",
           "is_fragile": true,
           "is_dangerous": false,
           "from_wh": "SOURCE WAREHOUSE",
           "to_wh": "DESTINATION WAREHOUSE",
           "template_id": "123456",
           "template_category": "ABCD",
           "invoice_reference": "TESTINV00001",
           "categories": "Footwear, Footwear",
           "otp_code": "174747",
           "insured": false,
           "customer_id_number": "111AAA6464646",
           "insurance_amount": 1125.55,
           "exchange_rvp_awb": "TESTRVPAWB00001",
           "rvp_reason": "Defective product",
           "qc_type": "doorstep",
           "order_date": "2022-11-21",
           "user_defined_field_array": [
               {{"name": "udf_1", "type": "String", "value": ""}},
               {{"name": "udf_2", "type": "String", "value": ""}},
               {{"name": "udf_3", "type": "String", "value": ""}},
               {{"name": "udf_4", "type": "String", "value": ""}}
           ]
       }},
       "gst_info": {{
           "seller_gstin": "1234",
           "taxable_value": 100,
           "ewaybill_serial_number": "2345677",
           "is_seller_registered_under_gst": false,
           "sgst_tax_rate": 100,
           "place_of_supply": "DELHI",
           "gst_discount": 0,
           "hsn_code": "1234",
           "sgst_amount": 100,
           "enterprise_gstin": "13",
           "gst_total_tax": 100,
           "igst_amount": 100,
           "cgst_amount": 200,
           "gst_tax_base": 200,
           "consignee_gstin": "1233",
           "igst_tax_rate": 100,
           "cgst_tax_rate": 100
       }}
   }}
   ```

   ### MPS, India only
   (source: https://docs.clickpost.ai/docs/order-creation-mps-v3-for-india-only)
   ```json
   {{
       "drop_info": {{
           "drop_lat": 0,
           "drop_city": "Mumbai",
           "drop_long": 0,
           "drop_name": "dsadsad sadsad",
           "drop_email": "something@something.com",
           "drop_phone": "9876543210",
           "drop_state": "Maharashtra",
           "drop_address": "123 ABC St",
           "drop_district": "Mumbai City",
           "drop_landmark": null,
           "drop_pincode": "400065",
           "drop_country": "IN",
           "drop_address_type": "OFFICE",
           "drop_vendor_code": "test_drop"
       }},
       "pickup_info": {{
           "pickup_lat": 25.1414664,
           "pickup_city": "Mumbai",
           "pickup_long": 55.255369,
           "pickup_name": "TEST DO NOT PICKUP",
           "pickup_time": "2022-10-27T11:52:36+04:00",
           "pickup_email": "abcd@defg.com",
           "pickup_phone": "1234567890",
           "pickup_state": "Maharashtra",
           "pickup_address": "123, ABC St",
           "pickup_district": "Mumbai City",
           "pickup_landmark": null,
           "pickup_pincode": "400065",
           "pickup_country": "IN",
           "pickup_address_type": "RESIDENTIAL"
       }},
       "shipment_details": {{
           "items": [
               {{
                   "sku": "53813",
                   "price": 249.29,
                   "weight": 500,
                   "hs_code": "6405.10.00",
                   "quantity": 1,
                   "description": "Tod's Blue Shoes",
                   "product_id": "11112222333444",
                   "variant_id": "55556666777888",
                   "additional": {{
                       "manufacture_country": "Italy",
                       "manufacture_country_code": "IT",
                       "cat": "Footwear",
                       "images": "http://link1.image.com,http://link2.image.com",
                       "product_url": "http://link1.product.com,http://link2.procuct.com",
                       "return_days": 2,
                       "exchange_days": 2,
                       "length": 25,
                       "breadth": 15,
                       "height": 11,
                       "brand": "Tod's",
                       "color": "blue",
                       "size": "39.5",
                       "serial_no": "25",
                       "imei": "AA-BBBBBB-CCCCCC-D",
                       "ean": "EAN123456",
                       "sub_category": "Flats",
                       "return_reason": "ABCD"
                   }},
                   "gst_info": {{
                       "hsn_code": "84713010",
                       "cgst_amount": "4193.1",
                       "igst_amount": "8386.2",
                       "sgst_amount": "4193.1",
                       "gst_discount": "0",
                       "seller_gstin": "34AACCA1237A1ZK",
                       "cgst_tax_rate": "9",
                       "gst_total_tax": "16772.4",
                       "igst_tax_rate": "18",
                       "sgst_tax_rate": "9",
                       "taxable_value": "46590",
                       "consignee_gstin": "09AAACH2702H1ZY",
                       "place_of_supply": "09",
                       "enterprise_gstin": "34AACCA1237A1ZK",
                       "ewaybill_serial_number": "NA",
                       "is_seller_registered_under_gst": true,
                       "invoice_reference": "TESTINV00001"
                   }}
               }},
               {{
                   "sku": "53814",
                   "price": 109.99,
                   "weight": 100,
                   "hs_code": "6405.10.21",
                   "quantity": 1,
                   "description": "Tod's Blue Shirt, Size L",
                   "product_id": "11112222333444",
                   "variant_id": "55556666777888",
                   "additional": {{
                       "manufacture_country": "Italy",
                       "manufacture_country_code": "IT",
                       "cat": "Footwear",
                       "images": "http://link1.image.com,http://link2.image.com",
                       "product_url": "http://link1.product.com,http://link2.procuct.com",
                       "return_days": 2,
                       "exchange_days": 2,
                       "length": 25,
                       "breadth": 15,
                       "height": 11,
                       "brand": "Tod's",
                       "color": "blue",
                       "size": "39.5",
                       "serial_no": "25",
                       "imei": "AA-BBBBBB-CCCCCC-D",
                       "ean": "EAN123456",
                       "sub_category": "Flats",
                       "return_reason": "ABCD"
                   }},
                   "gst_info": {{
                       "hsn_code": "84713010",
                       "cgst_amount": "4193.1",
                       "igst_amount": "8386.2",
                       "sgst_amount": "4193.1",
                       "gst_discount": "0",
                       "seller_gstin": "34AACCA1237A1ZK",
                       "cgst_tax_rate": "9",
                       "gst_total_tax": "16772.4",
                       "igst_tax_rate": "18",
                       "sgst_tax_rate": "9",
                       "taxable_value": "46590",
                       "consignee_gstin": "09AAACH2702H1ZY",
                       "place_of_supply": "09",
                       "enterprise_gstin": "34AACCA1237A1ZK",
                       "ewaybill_serial_number": "NA",
                       "is_seller_registered_under_gst": true,
                       "invoice_reference": "TESTINV00001"
                   }}
               }}
           ],
           "height": 13,
           "length": 23,
           "weight": 1000,
           "breadth": 31,
           "order_id": "TESTORDER0001",
           "cod_value": 50,
           "cod_currency_code": "INR",
           "order_type": "COD",
           "invoice_date": "2022-06-21",
           "delivery_type": "FORWARD",
           "invoice_value": 468.58,
           "invoice_number": "TLC-2022-06-WS-00038",
           "courier_partner": 3,
           "reference_number": "TESTECOM00001",
           "account_code": "1",
           "reseller_name": "ABCD",
           "booking_id": "123456"
       }},
       "additional": {{
           "return_info": {{
               "lat": 25.1414664,
               "city": "Mumbai",
               "long": 55.255369,
               "name": "TEST DO NOT RETURN",
               "email": "abcd@defg.com",
               "phone": "1234567890",
               "state": "Maharashtra",
               "address": "123, ABC St",
               "district": "Mumbai City",
               "landmark": "Near GHI Towers",
               "pincode": "400065",
               "country": "IN"
           }},
           "estimated_delivery_date": "2023-12-05",
           "async": false,
           "label": true,
           "vendor_code": "ABC",
           "zone": "ABC",
           "min_edd": 2,
           "max_edd": 5,
           "qc_type": "doorstep",
           "invoice_base_64": "JWBERKLhjfswifniouNuiHFGIUEHWOWNOFOINkjno2749189",
           "priority": "URGENT",
           "is_multi_seller": false,
           "channel_name": "Channel 1",
           "exchange_address": "123, XYZ Street",
           "exchange_city": "Delhi",
           "exchange_state": "Delhi",
           "exchange_country": "India",
           "exchange_pincode": "110001",
           "exchange_shipment_description": "Exchanged due to defective product",
           "otp_based_delivery": false,
           "inv_url": "http://link.to.invoice.com",
           "pickup_type": "WH",
           "is_fragile": true,
           "is_dangerous": false,
           "from_wh": "SOURCE WAREHOUSE",
           "to_wh": "DESTINATION WAREHOUSE",
           "template_id": "123456",
           "template_category": "ABCD",
           "invoice_reference": "TESTINV00001",
           "categories": "Footwear, Footwear",
           "otp_code": "174747",
           "insured": false,
           "customer_id_number": "111AAA6464646",
           "insurance_amount": 1125.55,
           "exchange_rvp_awb": "TESTRVPAWB00001",
           "fareye_store_id": "ST0001",
           "fareye_order_type": "ABC",
           "duty_fee_paid_by": "S",
           "order_date": "2022-11-21",
           "user_defined_field_array": [
               {{"name": "udf_1", "type": "String", "value": ""}},
               {{"name": "udf_2", "type": "String", "value": ""}},
               {{"name": "udf_3", "type": "String", "value": ""}},
               {{"name": "udf_4", "type": "String", "value": ""}}
           ]
       }},
       "gst_info": {{
           "seller_gstin": "1234",
           "seller_name": "ABCD Apparel",
           "taxable_value": 100,
           "ewaybill_serial_number": "2345677",
           "is_seller_registered_under_gst": false,
           "sgst_tax_rate": 100,
           "place_of_supply": "DELHI",
           "gst_discount": 0,
           "hsn_code": "1234",
           "sgst_amount": 100,
           "enterprise_gstin": "13",
           "gst_total_tax": 100,
           "igst_amount": 100,
           "cgst_amount": 200,
           "gst_tax_base": 200,
           "consignee_gstin": "1233",
           "igst_tax_rate": 100,
           "cgst_tax_rate": 100
       }}
   }}
   ```

   ### New B2B MPS
   (source: https://docs.clickpost.ai/docs/new-b2b-mps-order-creation-api)
   ```json
   {{
       "pickup_info": {{
           "name": "Mr Ramesh",
           "phone": "999999999",
           "district": "Chennai",
           "city": "Chennai",
           "landmark": "test_landmark",
           "organisation": "test_organisation",
           "state": "TamilNadu",
           "time": "2022-10-27T11:52:36+0530",
           "pincode": "600034",
           "country": "India",
           "address_type": "",
           "address": "NEW NO.150 OLD NO.60,RAMA Naiken STREET, Nungambakkam",
           "email": "abc@acme.in",
           "vendor_code": "teste_vendor_code",
           "location_code": "",
           "tin": "test"
       }},
       "drop_info": {{
           "organisation": "test_organisation",
           "district": "Puducherry",
           "address_type": "",
           "vendor_code": "",
           "name": "Mr Himesh",
           "landmark": "test_landamrk",
           "phone": "999999999",
           "email": "amol.chaudhari@acme.in",
           "city": "Puducherry",
           "state": "Puducherry",
           "pincode": "605001",
           "country": "India",
           "location_code": "",
           "address": "104, Kamaraj Salai, "
       }},
       "shipment_info": {{
           "description": "This is a big shipment",
           "reference_number": "TEST-112212932",
           "courier_partner": 129,
           "account_code": "Bluedart-prod1",
           "cod_value": 0,
           "order_type": "PREPAID",
           "order_id": "TESTorder_id1",
           "delivery_type": "FORWARD",
           "weight": 3000,
           "invoice_info": [
               {{"number": "inv-1", "value": 10000, "date": "2024-09-30", "base64": "", "ewaybill_serial_number": null, "ewaybill_expiry_date": null, "description": "Contains 36 qty"}},
               {{"number": "inv-2", "value": 10000, "date": "2024-09-30", "base64": "", "ewaybill_serial_number": null, "ewaybill_expiry_date": null, "description": "Contains 36 qty"}}
           ],
           "purchase_order_info": [
               {{"number": "po-1", "expiry_date": "2024-09-30"}},
               {{"number": "po-2", "expiry_date": "2024-09-30"}}
           ],
           "asn_info": [
               {{"asn_number": "asn-1", "asn_date": "2024-09-30"}}
           ],
           "box_info": [
               {{
                   "count": 5,
                   "reference_number_prefix": "ABC",
                   "code": "",
                   "height": 10,
                   "length": 10,
                   "weight": 10,
                   "breadth": 10,
                   "description": "test description",
                   "purchase_order_info": [{{"number": "po-1"}}],
                   "invoice_info": [{{"number": "inv-2"}}],
                   "product_info": [
                       {{"sku": "test_sku", "sku_count": "2", "description": "test description", "product_category": "", "manufacturer_country": "", "manufacturer_country_code": "", "hsn_code": "", "price": 10000}},
                       {{"sku": "test_sku", "sku_count": "2", "description": "test description", "product_category": "", "manufacturer_country": "", "manufacturer_country_code": "", "hsn_code": "", "price": 10000}}
                   ]
               }}
           ],
           "gst_info": {{
               "seller_gstin": "",
               "enterprise_gstin": "",
               "taxable_value": 0,
               "is_seller_registered_under_gst": null,
               "place_of_supply": "",
               "gst_discount": 0,
               "gst_total_tax": 0,
               "sgst_tax_rate": 0,
               "sgst_amount": 0,
               "igst_tax_rate": 0,
               "igst_amount": 0,
               "cgst_tax_rate": 0,
               "cgst_amount": 0,
               "consignee_gstin": "",
               "sgst_total_tax": 0
           }},
           "products_in_shipment": [
               {{"sku": "test_sku", "sku_count": "2", "description": "test description", "product_category": "", "manufacturer_country": "", "manufacturer_country_code": "", "hsn_code": "", "price": 10000}}
           ]
       }},
       "appointment_info": {{
           "start_time": "2023-05-24T10:00:00+0530",
           "end_time": "2023-05-24T12:00:00+0530",
           "id": ""
       }},
       "additional_info": {{
           "return_info": {{
               "lat": 20.45,
               "long": 40.54,
               "name": "CL-FC-CHN-PGR-Packing",
               "phone": "999999999",
               "district": "test_district",
               "city": "Chennai",
               "landmark": "test_landmark",
               "organisation": "test_organisation",
               "state": "TamilNadu",
               "time": "2022-10-27T11:52:36",
               "pincode": "600034",
               "country": "India",
               "address_type": "",
               "address": "NEW NO.150 OLD NO.60,RAMA Naiken STREET, Nungambakkam",
               "email": "amol.chaudhari@acme.in",
               "vendor_code": "teste_vendor_code",
               "location_code": "",
               "tin": "test"
           }},
           "is_label_required": true,
           "is_box_sticker_required": true,
           "is_multi_seller": false,
           "is_async": true,
           "zone": "",
           "order_date": "2024-10-11",
           "store_code": "",
           "user_defined_field_array": [
               {{"name": "udf_1", "type": "String", "value": ""}}
           ]
       }}
   }}
   ```

   ### Forward / SPS, rest of world (v4)
   (source: https://docs.clickpost.ai/docs/order-creation-v4)
   ```json
   {{
       "tax_info": {{
           "tax_paid": true,
           "tax_amount": 0,
           "shipper_tax_id": "100315930600003",
           "consignee_tax_id": "1234864968849",
           "consignee_tax_type": "UT",
           "consignee_tax_country_code": "MX",
           "exporter_tax_id": "1234864968849",
           "exporter_tax_type": "UT",
           "exporter_tax_country_code": "MX"
       }},
       "drop_info": {{
           "lat": 0,
           "city": "RUH",
           "long": 0,
           "name": "TEST DO NOT DELIVER",
           "organisation": "Tesla",
           "email": "something@something.com",
           "phone": "9876543210",
           "state": "Dubai",
           "address": "sadsa",
           "district": "Dubai",
           "landmark": "Near XYZ Park",
           "phone_code": "+965",
           "postal_code": "12345",
           "country_code": "SA"
       }},
       "pickup_info": {{
           "lat": 25.1414664,
           "city": "DXB",
           "long": 55.255369,
           "name": "TEST DO NOT PICKUP",
           "organisation": "Walmart",
           "time": "2022-07-01T11:52:36",
           "email": "abcd@defg.com",
           "phone": "1234567890",
           "state": "Dubai",
           "address": "123, Xyz Street, Near BNM Park",
           "district": "Dubai",
           "landmark": "Near LMN Building",
           "phone_code": "+971",
           "postal_code": "502626",
           "country_code": "AE"
       }},
       "shipment_details": {{
           "items": [
               {{
                   "sku": "53813",
                   "price": 249.29,
                   "weight": 500,
                   "hs_code": "6405.10.00",
                   "length": 25,
                   "breadth": 21,
                   "height": 6,
                   "quantity": 1,
                   "description": "Tod's Blue Patent Cutout Ankle Strap Ballet Flats Size 39.5",
                   "manufacture_country": "Italy",
                   "manufacture_country_code": "IT",
                   "cat": "Footwear",
                   "images": "http://link1.image.com,http://link2.image.com",
                   "product_url": "http://link1.product.com,http://link2.procuct.com",
                   "return_days": 2,
                   "exchange_days": 2,
                   "color": "dark blue",
                   "brand": "Tod's",
                   "size": "39.5",
                   "sub_category": "Flats",
                   "return_reason": "Defective",
                   "special_instructions": "Handle with care",
                   "imei": "12345678910111",
                   "ean": "EAN123456"
               }}
           ],
           "height": 13,
           "length": 23,
           "weight": "1000",
           "breadth": 31,
           "order_id": "Test.UPS.Order0002",
           "cod_value": 0,
           "order_type": "PREPAID",
           "invoice_date": "2022-06-21",
           "account_code": "test",
           "currency_code": "USD",
           "delivery_type": "RVP",
           "invoice_value": 468.58,
           "invoice_number": "TLC-2022-06-WS-00038",
           "courier_partner": 123,
           "rvp_reason": "Cancelled",
           "reference_number": "test_courier_00001",
           "reseller_name": "Test Reseller",
           "otp_based_delivery": true,
           "exchange_address": "123, Test address"
       }},
       "additional": {{
           "return_info": {{
               "lat": 25.1414664,
               "city": "Dubai",
               "long": 55.255369,
               "name": "TEST DO NOT RETURN",
               "email": "abcd@defg.com",
               "phone": "1234567890",
               "state": "Dubai",
               "address": "123, ABC St",
               "district": "Dubai",
               "landmark": "Near LMN Building",
               "phone_code": "+965",
               "postal_code": "502626",
               "country_code": "KW"
           }},
           "estimated_delivery_date": "2023-12-05",
           "label": true,
           "priority": "NORMAL",
           "invoice_base_64": "JVBERaolfnawofikwenfoifoeirf",
           "order_date": "2022-11-21",
           "user_defined_field_array": [
               {{"name": "pickup_address_code", "type": "String", "value": "ABCD1234"}},
               {{"name": "drop_address_code", "type": "String", "value": "EADA4540"}}
           ]
       }},
       "courier_custom_fields": {{}}
   }}
   ```

   ### MPS, rest of world (v4)
   (source: https://docs.clickpost.ai/docs/order-creation-mps-v4-for-international-use)
   ```json
   {{
       "additional": {{
           "channel": "",
           "async": false,
           "duty_fee_paid_by": "R",
           "invoice_base_64": "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiPz4KPEVycm9yPjxDb2RlPkFjY2Vzc0RlbmllZDwvQ29kZT48TWVzc2FnZT5BY2Nlc3MgRGVuaWVkPC9NZXNzYWdlPjxSZXF1ZXN0SWQ+WThIR1pGN0FYS1hBRkQxQzwvUmVxdWVzdElkPjxIb3N0SWQ+YWhxenVzMG5Ycnc2aDM3dzVSWnhxUXBsblpQdFhPZzY2SnJBaGdScjd4cW5xVFVYOGNBUk50Yjl4bi9RNnYrdms2NGZGK3BqUjg4PTwvSG9zdElkPjwvRXJyb3I+",
           "is_fragile": false,
           "estimated_delivery_date": "2023-12-05",
           "label": true,
           "max_edd": "10",
           "order_date": "2023-08-21",
           "return_info": {{
               "address": "Naman Midtown - A Wing - 18th floor Senapati Bapat Marg, Prabhadevi",
               "city": "Mumbai",
               "country_code": "IN",
               "district": "",
               "email": "self@clickpost.com",
               "landmark": "",
               "lat": null,
               "long": null,
               "name": "Lokesh",
               "phone": "6285657600",
               "phone_code": "+91",
               "postal_code": "400013",
               "state": "Maharashtra"
           }},
           "user_defined_field_array": [
               {{"name": "pickup_address_code", "type": "String", "value": "ABCD1234"}},
               {{"name": "drop_address_code", "type": "String", "value": "EADA4540"}}
           ]
       }},
       "courier_custom_fields": {{}},
       "drop_info": {{
           "address": "usa",
           "city": "New York",
           "country_code": "US",
           "district": "",
           "email": "sehdev@google.com",
           "landmark": "",
           "lat": null,
           "long": null,
           "name": "Sehdev",
           "phone": "8446313562",
           "phone_code": "+1",
           "postal_code": "10001",
           "state": "NY"
       }},
       "pickup_info": {{
           "address": "Naman Midtown - A Wing - 18th floor Senapati Bhawan, Mahesmati",
           "city": "Mumbai",
           "country_code": "IN",
           "district": "",
           "email": "self@clickpost.ai",
           "landmark": "",
           "lat": null,
           "long": null,
           "name": "Clickpost HQ",
           "phone": "02262808699",
           "phone_code": "+91",
           "postal_code": "400013",
           "state": "MH",
           "time": "2023-08-22T17:00:00"
       }},
       "shipment_details": {{
           "account_code": "Fedex Bundle Duty paid by Receiver",
           "breadth": 8,
           "cod_value": 0,
           "courier_partner": 330,
           "currency_code": "INR",
           "delivery_type": "FORWARD",
           "height": 13,
           "invoice_date": "2023-08-21",
           "invoice_number": "12324",
           "invoice_url": "http://link.to.inv1.com",
           "invoice_value": 9000,
           "items": [
               {{
                   "breadth": 8,
                   "cat": "Customized Fashion Garments",
                   "description": "Floral Print Anarkali With Dupatta",
                   "fob_value": 9000,
                   "images": "https://fb.com/products/22/8/192005329771_BTM_RAW_1.JPG",
                   "product_url": "https://fb.com/products/22/8/192005329771_BTM_RAW_1.JPG",
                   "gst_info": {{"igst_amount": 0, "igst_tax_rate": 0, "taxable_value": 9000}},
                   "height": 13,
                   "hs_code": "44209090",
                   "length": 13,
                   "manufacture_country": "INDIA",
                   "manufacture_country_code": "IN",
                   "price": 9000,
                   "product_category": "Customized Fashion Garments",
                   "quantity": 1,
                   "sku": "615168",
                   "sub_category": "Customized Fashion Garments",
                   "weight": 120,
                   "width": 0
               }}
           ],
           "items_in_shipment": [
               {{
                   "box_number": 1,
                   "breadth": 8,
                   "cat": "Customized Fashion Garments",
                   "description": "Floral Print Anarkali With Dupatta",
                   "fob_value": 9000,
                   "exchange_days": 2,
                   "gst_info": {{"igst_amount": 0, "igst_tax_rate": 0, "taxable_value": 9000}},
                   "height": 13,
                   "hs_code": "44209090",
                   "length": 13,
                   "manufacture_country": "INDIA",
                   "manufacture_country_code": "IN",
                   "price": 9000,
                   "product_category": "Customized Fashion Garments",
                   "quantity": 1,
                   "sku": "615168",
                   "sub_category": "Customized Fashion Garments",
                   "weight": 120,
                   "width": 0,
                   "images": "http://link1.image.com,http://link2.image.com",
                   "product_url": "http://link1.product.com,http://link2.procuct.com",
                   "return_days": 2
               }}
           ],
           "length": 13,
           "order_id": "304491",
           "order_type": "PREPAID",
           "reference_number": "304491-12324-14",
           "weight": 120
       }},
       "tax_info": {{
           "consignee_tax_country_code": "US",
           "consignee_tax_id": "NA",
           "consignee_tax_type": "VAT",
           "consignee_tax_type_country_code": "US",
           "exporter_tax_country_code": "IN",
           "exporter_tax_id": "27AAACO7149M1ZZ",
           "exporter_tax_type": "VAT",
           "exporter_tax_type_country_code": "IN",
           "exporter_type": "",
           "import_duty_vat_charges": 0,
           "shipper_tax_id": "27AAACO7149M1ZZ",
           "shipping_charges": 0,
           "tax_amount": 0,
           "tax_paid": true
       }}
   }}
   ```
   Use the EXACT field names and nesting from whichever sample payload above matches this integration (e.g. `pickup_info.pickup_name`) — never invent your own names, and never let the two sides of the same physical field (e.g. pickup vs drop) drift into different naming conventions. Drop-side fields must follow the identical pattern used on the pickup side (`drop_info.drop_name` mirrors `pickup_info.pickup_name`, etc.) — generic terms like `consignee`/`receiver` are never correct.

PRODUCE A SINGLE MARKDOWN FILE WITH EXACTLY THESE SECTIONS, IN THIS ORDER:

1. Title(Only Courier Name) + 1-2 line plain-English description of what this courier partner does (in the style of Clickpost's own courier-partner pages, e.g. "<Partner> is a last-mile delivery courier partner offering ...") and CPID of the Courier in new line

2. "Services Integrated" — a plain bullet list naming ONLY the capabilities the context bundle proves are actually wired up for this partner, drawn from: Order Creation, Order Cancellation, Tracking, Pre-assigned AWB, Vendor Code Lookup, OTP-based Delivery. Omit anything not proven by the bundle — do not list every possible service, and never mention labels/label generation. This mirrors the "Services Integrated" list on Clickpost's own courier-partner pages.

3. "Steps to setup <Partner> on Clickpost" — one row per field exposed via the credential model's `get_model_fields()`/`get_model_field_type()` (found in the context bundle's CREDENTIAL MODEL section). For each: field name (as shown on the Clickpost dashboard), a plain-English definition of what it is, and why Clickpost needs it. Do NOT list system-managed fields (tokens, expiry timestamps) as something the onboarding team needs to collect — only what a human actually has to type in,
also if an enum values are present also add those values for possible inputs.

also add these steps before this map for 

Steps to Set Up <Courier name> on Clickpost
First, complete the process of setting up a courier partner account by following the steps outlined here: https://docs.clickpost.ai/docs/create-an-courier-partner-accountPartner 

Once your account is created, navigate to Carrier Integrations → <courier name> and open your account configuration. You will land on the Credentials screen.


4. "Sample Clickpost Request — Create a <Partner> Order" — one realistic, complete JSON sample using the exact request structure/nesting from the Clickpost reference sample payload selected in Step 2, with `"courier_partner": {cpid}` set at the correct nesting level for that structure so it will create the order if used in real api call. Output ONLY the JSON code block for this section which should look like a fenced code block when previewed on ReadMe — do not add any prose notes, caveats, or explanations before or after it. In particular, do NOT add anything like: which fields are "most critical", account_code-matching caveats, reference_number-uniqueness caveats, lists of fields "mandatory on Clickpost's API but not forwarded" to the partner, or tracking-number/waybill behavior explanations, and also if you see certain clickpost field can fill the courier partner request value but is is hardcoded call out that field with commenting that line and showing the value we are passing (that belongs in Section 6, not here).

5. "Onboarding Checklist" — a literal bullet point covering: collecting credentials, entering them on the dashboard, any one-time webhook setup (only if applicable), and doing an end-to-end test shipment before going live. Keep this to concrete, partner-specific action items only — no generic filler steps.


6. "Notes" - Add a notes at end like do we need any preassigned flow enabled for this along with the vendor code for example like this """
Note:
AWB Generation:
Labels:
CSB/4 Support: 
 """

RULES:
- Every claim about what is/isn't supported must be traceable to something in the context bundle — never assume a capability (OTP, pre-assigned AWB, vendor code, multi-currency, etc.) exists unless the code shows it.
- Never mention labels or label generation anywhere in the document, whether supported or not — this is out of scope for this doc entirely.
- No code snippets, class names, exception names, or file paths anywhere in the output.
- Never print any URL (partner doc link, Clickpost doc link, or Clickpost's own courier-partner-page examples) anywhere in the output — these are inputs/style references for research only, not reader-facing content.
- Drop-side Clickpost field names MUST mirror the pickup-side naming pattern exactly — if pickup fields are `pickup_info.pickup_name`/`pickup_info.pickup_phone`/etc., drop fields MUST be `drop_info.drop_name`/`drop_info.drop_phone`/etc. Never substitute generic-sounding names like `consignee.name` or `receiver.phone` — these are not real Clickpost fields, even if they sound plausible.
- The sample JSON in Section 7 must use the exact nested structure from the Clickpost reference sample payload selected in Step 2 — do not flatten fields to the request root that belong inside a nested object (e.g. `shipment_details`, `pickup_info`) per that reference, and do not add fields that don't appear in that reference sample.
- Section 7 must contain ONLY the JSON code block — no surrounding explanatory paragraphs, bullet notes, or caveats of any kind.
- If something in the bundle is genuinely ambiguous (a WARNING marker is present because the credential model or tracking webhook wasn't auto-resolved), state the gap plainly in one sentence in a short closing note — but never name or print the specific doc URL that was inaccessible.


Note- Please don't add lines betweens sections