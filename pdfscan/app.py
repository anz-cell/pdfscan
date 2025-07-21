import os
import time
import requests
from flask import Flask, request, render_template, redirect, url_for
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

MONGO_URI = 'mongodb+srv://myrealnameisabdullah:3NpMi44K9CSEANN8@cluster0.dwj1mqk.mongodb.net/'
DB_NAME = 'pdfscan_db'
COLLECTION_NAME = 'pdfs'

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

API_KEY = 'LTEyNDc2NzUxMjY=_3b7441rtz5a80k5c2lblo'
API_URL_BASE = "https://api.extracta.ai/api/v1"

def create_extraction():
    url = f"{API_URL_BASE}/createExtraction"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    extraction_details = {
        "extractionDetails": {
            "name": "Invoice Data Extraction",
            "description": "Extract fields from invoice PDFs",
            "language": "English",
            "options": {
                "hasTable": True,
                "handwrittenTextRecognition": False,
                "checkboxRecognition": False
            },
            "fields": [
                { "key": "invoice_number", "example": "1" },
                { "key": "seller", "example": "Me" },
                { "key": "bill_to", "example": "Human" },
                { "key": "ship_to", "example": "Abdu DHabi" },
                { "key": "invoice_date", "example": "Jul 16, 2025" },
                { "key": "payment_terms", "example": "Card" },
                { "key": "due_date", "example": "Jul 23, 2025" },
                { "key": "po_number", "example": "3033" },
                { "key": "item_1_description", "example": "Item 1" },
                { "key": "item_1_quantity", "example": "123" },
                { "key": "item_1_rate", "example": "AED 334.00" },
                { "key": "item_1_amount", "example": "AED 41,082.00" },
                { "key": "subtotal", "example": "AED 41,082.00" },
                { "key": "discount", "example": "AED 18,486.90" },
                { "key": "tax", "example": "AED 1,129.76" },
                { "key": "shipping", "example": "AED 34.00" },
                { "key": "total", "example": "AED 23,758.86" },
                { "key": "amount_paid", "example": "AED 20,000.00" },
                { "key": "balance_due", "example": "AED 3,758.86" },
                { "key": "notes", "example": "New" },
                { "key": "terms", "example": "New items" }
            ]
        }
    }
    response = requests.post(url, json=extraction_details, headers=headers)
    response.raise_for_status()
    return response.json().get("extractionId")

def upload_file_to_extraction(extraction_id, filepath):
    url = f"{API_URL_BASE}/uploadFiles"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    with open(filepath, "rb") as f:
        files = {"files": (os.path.basename(filepath), f, "application/octet-stream")}
        data = {"extractionId": extraction_id}
        response = requests.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        return response.json()

def get_batch_results(extraction_id, batch_id):
    url = f"{API_URL_BASE}/getBatchResults"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    payload = {"extractionId": extraction_id, "batchId": batch_id}
    time.sleep(2)
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()

@app.route("/", methods=["GET", "POST"])
def upload_invoice():
    if request.method == "POST":
        if 'invoice' not in request.files:
            return "No file uploaded"
        file = request.files['invoice']
        if file.filename == '':
            return "No file selected"
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            extraction_id = create_extraction()
            upload_result = upload_file_to_extraction(extraction_id, filepath)
            batch_id = upload_result.get("batchId")

            result = get_batch_results(extraction_id, batch_id)
            files = result.get("files", [])
            if not files:
                # Save basic info so we can retry later
                document = {
                    "filename": filename,
                    "filepath": filepath,
                    "extractionId": extraction_id,
                    "batchId": batch_id,
                    "status": "processing",
                    "uploadedAt": datetime.utcnow()
                }
                collection.insert_one(document)
                return redirect(url_for('retry_result', extraction_id=extraction_id, batch_id=batch_id))


            structured_data = files[0].get("result", {})

            # Transform flat item_1_* keys into items array
            item_pattern = re.compile(r'item_(\d+)_(\w+)')
            items_dict = {}
            keys_to_delete = []

            for key, value in structured_data.items():
                match = item_pattern.match(key)
                if match:
                    idx, field = match.groups()
                    idx = int(idx)
                    items_dict.setdefault(idx, {})[field] = value
                    keys_to_delete.append(key)

            for key in keys_to_delete:
                del structured_data[key]

            if items_dict:
                structured_data["items"] = [items_dict[k] for k in sorted(items_dict.keys())]

            document = {
                "filename": filename,
                "filepath": filepath,
                "extractionId": extraction_id,
                "batchId": batch_id,
                "upload_result": upload_result,
                "extracted_data": structured_data,
                "uploadedAt": datetime.utcnow()
            }
            collection.insert_one(document)
            return f"✅ File processed and saved.<br><pre>{structured_data}</pre>"

        except Exception as e:
            return f"❌ Error: {e}"

    return render_template("upload.html")

@app.route("/retry/<extraction_id>/<batch_id>")
def retry_result(extraction_id, batch_id):
    try:
        result = get_batch_results(extraction_id, batch_id)
        files = result.get("files", [])
        if not files:
            return "🕒 Still processing. Try again shortly."
        structured_data = files[0].get("result", {})

        collection.update_one(
            {"extractionId": extraction_id},
            {"$set": {"extracted_data": structured_data, "status": "complete"}}
        )

        return f"✅ Data extraction complete.<br><pre>{structured_data}</pre>"
    except Exception as e:
        return f"❌ Retry failed: {e}"

if __name__ == "__main__":
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(debug=True)
