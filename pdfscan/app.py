from flask import Flask, request, redirect, url_for, render_template, flash, render_template_string
import os
from werkzeug.utils import secure_filename
import PyPDF2
from pymongo import MongoClient
import pdfplumber
import re

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}

MONGO_URI = 'mongodb+srv://myrealnameisabdullah:3NpMi44K9CSEANN8@cluster0.dwj1mqk.mongodb.net/'
DB_NAME = 'pdfscan_db'
COLLECTION_NAME = 'pdfs'

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'supersecretkey'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
pdfs_collection = db[COLLECTION_NAME]

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return 'PDFScan Flask App is running!'

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # PDF parsing with pdfplumber
            with pdfplumber.open(filepath) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or ""

            # Clean and split lines
            lines = [l.strip() for l in text.splitlines() if l.strip()]

            # Helper functions
            def get_value_regex(pattern, lines):
                for line in lines:
                    m = re.search(pattern, line)
                    if m:
                        return m.group(1).strip()
                return None

            def get_value_right_of_label(label, lines):
                for line in lines:
                    if label in line:
                        parts = line.split(label, 1)
                        if len(parts) == 2 and parts[1].strip():
                            return parts[1].strip()
                return None

            def get_summary_value(label, lines):
                for line in lines:
                    if label in line:
                        idx = line.rfind(':')
                        if idx != -1:
                            value = line[idx+1:].strip()
                            if value:
                                return value
                return None

            def get_next_line_after_label(label, lines):
                for i, line in enumerate(lines):
                    if line.strip() == label and i+1 < len(lines):
                        next_line = lines[i+1].strip()
                        if next_line:
                            return next_line
                return None

            # Debug output
            print('--- PDF Lines ---')
            for idx, l in enumerate(lines):
                print(f'{idx}: {l}')
            print('-----------------')

            # 'From' extraction
            from_field = None
            if lines:
                first_line = lines[0]
                if 'INVOICE' in first_line:
                    from_field = first_line.split('INVOICE')[0].strip()
                else:
                    from_field = first_line.strip()

            # Invoice Number extraction (fixed)
            invoice_number = get_value_regex(r'#\s*(\d+)', lines)
            if not invoice_number:
                for line in lines:
                    m = re.search(r'#\s*(\d+)', line)
                    if m:
                        invoice_number = m.group(1)
                        print(f'Invoice number fallback found: {invoice_number}')
                        break

            # Date and other fields
            date = get_value_right_of_label('Date:', lines)
            payment_terms = get_value_right_of_label('Payment Terms:', lines)
            due_date = get_value_right_of_label('Due Date:', lines)
            po_number = get_value_right_of_label('PO Number:', lines)
            balance_due = get_value_right_of_label('Balance Due:', lines)

            # Bill To and Ship To (fixed)
            bill_to = None
            ship_to = None
            for i, line in enumerate(lines):
                if 'Bill To:' in line and 'Ship To:' in line:
                    if i + 2 < len(lines):
                        candidate_line = lines[i + 2].strip()
                        print(f'Bill/Ship To candidate line: {candidate_line}')
                        words = candidate_line.split()
                        if len(words) >= 2:
                            bill_to = words[0]
                            ship_to = ' '.join(words[1:])
                    break

            # Items
            items = []
            try:
                item_start = next(i for i, l in enumerate(lines) if l.startswith('Item'))
                for j in range(item_start+1, len(lines)):
                    if lines[j].startswith('Subtotal:'):
                        break
                    m = re.match(r'(.+?)\s+(\d+)\s+([A-Z]{3} [\d,\.]+)\s+([A-Z]{3} [\d,\.]+)', lines[j])
                    if m:
                        items.append({
                            'item': m.group(1),
                            'quantity': m.group(2),
                            'rate': m.group(3),
                            'amount': m.group(4)
                        })
            except StopIteration:
                pass

            # Summary
            subtotal = get_summary_value('Subtotal', lines)
            discount = get_summary_value('Discount', lines)
            tax = get_summary_value('Tax', lines)
            shipping = get_summary_value('Shipping', lines)
            total = get_summary_value('Total', lines)
            amount_paid = get_summary_value('Amount Paid', lines)

            # Notes and Terms
            notes = get_next_line_after_label('Notes:', lines)
            terms = get_next_line_after_label('Terms:', lines)

            # Final invoice dict
            invoice_data = {
                'from': from_field,
                'bill_to': bill_to,
                'ship_to': ship_to,
                'invoice_number': invoice_number,
                'date': date,
                'payment_terms': payment_terms,
                'due_date': due_date,
                'po_number': po_number,
                'balance_due': balance_due,
                'items': items,
                'subtotal': subtotal,
                'discount': discount,
                'tax': tax,
                'shipping': shipping,
                'total': total,
                'amount_paid': amount_paid,
                'notes': notes,
                'terms': terms
            }

            # Store in MongoDB
            pdf_doc = {
                'filename': filename,
                'text_length': len(text),
                'num_pages': len(pdf.pages),
                'raw_lines': lines
            }
            pdf_doc.update(invoice_data)
            pdfs_collection.insert_one(pdf_doc)

            # Delete uploaded file
            if os.path.exists(filepath):
                os.remove(filepath)

            flash(f"File uploaded and invoice data extracted!")
            return redirect(url_for('upload_file'))
    return render_template_string('''
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Upload PDF</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f4f4f9; margin: 0; padding: 0; }
            .container { max-width: 400px; margin: 60px auto; background: #fff; padding: 30px 40px 40px 40px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
            h1 { text-align: center; color: #333; }
            form { display: flex; flex-direction: column; gap: 15px; }
            input[type=file] { padding: 8px; }
            input[type=submit] { background: #4f8cff; color: #fff; border: none; padding: 10px; border-radius: 5px; cursor: pointer; font-size: 16px; }
            input[type=submit]:hover { background: #2563eb; }
            .msg { color: #2563eb; text-align: center; margin-bottom: 10px; }
            .nav { text-align: center; margin-top: 20px; }
            .nav a { color: #4f8cff; text-decoration: none; margin: 0 10px; }
            .nav a:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Upload PDF File</h1>
            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="msg">{{ messages[0] }}</div>
              {% endif %}
            {% endwith %}
            <form method="post" enctype="multipart/form-data">
                <input type="file" name="file" required>
                <input type="submit" value="Upload">
            </form>
            <div class="nav">
                <a href="/pdfs">View Stored PDFs</a>
            </div>
        </div>
    </body>
    </html>
    ''')

@app.route('/pdfs')
def list_pdfs():
    pdfs = list(pdfs_collection.find())
    html = '''
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Stored PDFs</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f4f4f9; margin: 0; padding: 0; }
            .container { max-width: 800px; margin: 60px auto; background: #fff; padding: 30px 40px 40px 40px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
            h1 { text-align: center; color: #333; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { padding: 12px 8px; border-bottom: 1px solid #e0e0e0; text-align: left; }
            th { background: #4f8cff; color: #fff; }
            tr:hover { background: #f1f7ff; }
            .nav { text-align: center; margin-top: 20px; }
            .nav a { color: #4f8cff; text-decoration: none; margin: 0 10px; }
            .nav a:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Stored PDFs</h1>
            <table>
                <tr><th>Filename</th><th>Pages</th><th>Text Length</th></tr>'''
    for pdf in pdfs:
        html += f"<tr><td>{pdf.get('filename')}</td><td>{pdf.get('num_pages')}</td><td>{pdf.get('text_length')}</td></tr>"
    html += '''
            </table>
            <div class="nav">
                <a href="/upload">Upload another PDF</a>
            </div>
        </div>
    </body>
    </html>
    '''
    return html

if __name__ == '__main__':
    app.run(debug=True)
