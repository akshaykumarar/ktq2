#!/usr/bin/env bash
# Demonstration Script: Submitting Vendor Quotations across diverse channels and formats

BASE_URL="http://127.0.0.1:8000"
SAMPLES_DIR="$(cd "$(dirname "$0")/samples" && pwd)"

echo "================================================================================"
echo "AI-QL VENDOR QUOTATION INTAKE & EXTRACTION DEMO"
echo "================================================================================"

# 1. Submit Excel Quotation with Attachments
echo -e "\n1. Submitting Multi-format Quotation (Excel + Phone Photo):"
curl -s -X POST "${BASE_URL}/api/vendor-responses" \
  -F "files=@${SAMPLES_DIR}/sample_1_restructured_excel.xlsx" \
  -F "files=@${SAMPLES_DIR}/sample_4_phone_photo_usd_ratecard.png" \
  -F "rfx_ref=1" \
  -F "sender_name=Packaging Solutions Pvt Ltd" \
  -F "sender_email=sales@packagingsolutions.in" \
  -F "channel=upload" | python3 -m json.tool

# 2. Submit Email with Quoted References and Text Body
echo -e "\n2. Submitting Email Body Quotation (with 'same as last year' phrase):"
curl -s -X POST "${BASE_URL}/api/vendor-responses" \
  -F "body_text=Hi Team, quoting for RFX #1: Corrugated Box 600x400x300 mm at Rs 43.20/pc, Tape is same as last year rate. Freight extra." \
  -F "subject=Re: RFX #1 Corrugated Packaging Quotation" \
  -F "sender_name=Sharma Packaging & Co." \
  -F "sender_email=rajesh@sharmapackaging.in" \
  -F "channel=email" | python3 -m json.tool

# 3. Submit PDF Quotation on Letterhead
echo -e "\n3. Submitting Formal PDF Quotation on Letterhead:"
curl -s -X POST "${BASE_URL}/api/vendor-responses" \
  -F "files=@${SAMPLES_DIR}/sample_2_letterhead_quote.pdf" \
  -F "rfx_ref=1" \
  -F "channel=upload" | python3 -m json.tool

# 4. Check Response Status
echo -e "\n4. Checking Vendor Response Status (Response ID #1):"
curl -s -X GET "${BASE_URL}/api/vendor-responses/1" | python3 -m json.tool

# 5. Retrieve Extracted Line Items with Normalized Prices & Flags
echo -e "\n5. Retrieving Extracted Items with Flags & State:"
curl -s -X GET "${BASE_URL}/api/vendor-responses/1/items" | python3 -m json.tool

# 6. Retrieve Visual Evidence Crop
echo -e "\n6. Retrieving Visual Evidence Crop for Item #1:"
curl -s -o /dev/null -w "Crop image HTTP status: %{http_code}\n" -X GET "${BASE_URL}/api/response-items/1/crop"

# 7. Apply Buyer Correction on an Item
echo -e "\n7. Applying Buyer Correction on Item #1 (Adjusting price):"
curl -s -X PATCH "${BASE_URL}/api/response-items/1" \
  -H "Content-Type: application/json" \
  -d '{"corrected_price_inr": 41.50, "review_status": "corrected", "reviewed_by": "Senior Buyer", "notes": "Negotiated volume discount rate."}' | python3 -m json.tool

# 8. View RFx Coverage Summary Across All Vendors
echo -e "\n8. Viewing RFx #1 Coverage Matrix across all Quoting Vendors:"
curl -s -X GET "${BASE_URL}/api/rfx/1/responses" | python3 -m json.tool

echo -e "\n================================================================================"
echo "DEMO COMPLETE"
echo "================================================================================"
