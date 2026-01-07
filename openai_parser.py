from flask import Flask, request, jsonify
from io import BytesIO
import fitz
import re
from openai import OpenAI
import os
import base64
import docx2txt
import traceback

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

def extract_text_from_doc(blob):
    try:
        text = docx2txt.process(BytesIO(blob))
        if not text:
            return None
        doc_str = re.sub(r'\s[,.]', ',', text)
        doc_str = re.sub(r'[\n]+', '\n', doc_str)
        doc_str = re.sub(r'[\s]+', ' ', doc_str)
        doc_str = re.sub(r'http[s]?(://)?', '', doc_str)
        doc_str = re.sub(r'[^\x00-\x7F]+', '', doc_str)
        return doc_str
    except Exception as e:
        print(f"DOCX extraction error: {e}")
        traceback.print_exc()
        return None

def extract_text_from_pdf(blob):
    try:
        pdf_data = fitz.open(stream=blob, filetype="pdf")
        all_text = ''
        for page_num in range(pdf_data.page_count):
            page = pdf_data.load_page(page_num)
            text = page.get_text("text")
            all_text += text + '\n\n'
        pdf_data.close()

        if not all_text.strip():
            return None

        pdf_str = re.sub(r'\s[,.]', ',', all_text)
        pdf_str = re.sub(r'[\n]+', '\n', pdf_str)
        pdf_str = re.sub(r'[\s]+', ' ', pdf_str)
        pdf_str = re.sub(r'http[s]?(://)?', '', pdf_str)
        pdf_str = re.sub(r'[^\x00-\x7F]+', '', pdf_str)
        return pdf_str
    except Exception as e:
        print(f"PDF extraction error: {e}")
        traceback.print_exc()
        return None

def detect_extension_from_blob(blob, file_extension):
    try:
        ext = file_extension.lower().strip()
        if ext == 'pdf':
            return extract_text_from_pdf(blob)
        elif ext in ['docx', 'doc']:
            return extract_text_from_doc(blob)
        else:
            print(f"Unsupported file type: {ext}")
            return None
    except Exception as e:
        print(f"Extension detection error: {e}")
        return None

@app.route('/upload', methods=['POST'])
def upload_blob():
    # Validate request
    if 'application/json' not in request.headers.get('Content-Type', ''):
        return jsonify({'error': 'Invalid content type. Expected application/json'}), 400

    try:
        data = request.get_json()
        file_extension = data.get('type')
        encoded_blob = data.get('encoded_blob')

        # Validate required fields
        if not file_extension:
            return jsonify({'error': 'Missing "type" field'}), 400
        if not encoded_blob:
            return jsonify({'error': 'Missing "encoded_blob" field'}), 400

        # Decode base64
        try:
            blob_data = base64.b64decode(encoded_blob)
        except Exception as e:
            return jsonify({'error': f'Invalid base64 data: {str(e)}'}), 400

        # Extract text
        pdf_text = detect_extension_from_blob(blob_data, file_extension)

        # ✅ FIX: Check if extraction failed
        if pdf_text is None or pdf_text.strip() == '':
            return jsonify({
                'error': 'Text extraction failed. File may be corrupted, password-protected, or unsupported format.',
                'PersonalInformation': {'Name': '', 'Email': '', 'Phone': '', 'Address': '', 'Location': ''},
                'Skills': []
            }), 400

        # Call OpenAI
        os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
        client = OpenAI()

        prmpt = """you will be provided with resume text and your task is to parse resume details very precisely and generate output in json format like this.\n{
        "PersonalInformation":{"Name":"","Email":"","Phone":"","Address":"","Location":""},
        "Skills":[]
        } \n\nresume_text:\n""" + pdf_text

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "you are a resume parser assistant and only gives result as output without specifying anything."},
                {"role": "user", "content": prmpt}
            ]
        )

        return response.choices[0].message.content, 200

    except Exception as e:
        print(f"Error in /upload: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/uploads', methods=['POST'])
def upload_blobParser():
    # Validate request
    if 'application/json' not in request.headers.get('Content-Type', ''):
        return jsonify({'error': 'Invalid content type. Expected application/json'}), 400

    try:
        data = request.get_json()
        file_extension = data.get('type')
        encoded_blob = data.get('encoded_blob')

        # Validate required fields
        if not file_extension:
            return jsonify({'error': 'Missing "type" field'}), 400
        if not encoded_blob:
            return jsonify({'error': 'Missing "encoded_blob" field'}), 400

        # Decode base64
        try:
            blob_data = base64.b64decode(encoded_blob)
        except Exception as e:
            return jsonify({'error': f'Invalid base64 data: {str(e)}'}), 400

        print(f"Received file type: {file_extension}")
        print(f"Blob size: {len(blob_data)} bytes")

        # Extract text
        pdf_text = detect_extension_from_blob(blob_data, file_extension)

        # ✅ FIX: Check if extraction failed BEFORE concatenating
        if pdf_text is None or pdf_text.strip() == '':
            return jsonify({
                'error': 'Text extraction failed. File may be corrupted, password-protected, or unsupported format.',
                'personalInfo': {
                    'firstName': '', 'lastName': '', 'email': '', 
                    'phone': '', 'mobile': '', 'linkedIn': '',
                    'address': {'street': '', 'city': '', 'state': '', 'postalCode': '', 'country': ''}
                },
                'workHistory': [],
                'education': [],
                'skills': [],
                'certifications': [],
                'summary': '',
                'totalYearsExperience': 0
            }), 400

        print(f"Extracted text length: {len(pdf_text)}")
        print(f"First 500 chars: {pdf_text[:500]}")

        # Call OpenAI
        os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
        client = OpenAI()

        prmpt = """you will be provided with resume text and your task is to parse resume details very precisely and extract ALL information. 
Return ONLY a valid JSON object with the EXACT structure shown below.

{
"personalInfo": {
    "firstName": "string",
    "lastName": "string",
    "email": "string",
    "phone": "string",
    "mobile": "string",
    "linkedIn": "string",
    "address": {
    "street": "",
    "city": "",
    "state": "",
    "postalCode": "",
    "country": ""
    }
},
"workHistory": [
    {
    "company": "",
    "title": "",
    "startDate": "YYYY-MM",
    "endDate": "YYYY-MM or Present",
    "description": ""
    }
],
"education": [
    {
    "institution": "",
    "degree": "",
    "major": "",
    "graduationYear": "YYYY"
    }
],
"skills": [],
"certifications": [],
"summary": "",
"totalYearsExperience": 0
}

IMPORTANT:
- Extract ALL jobs and ALL education entries.
- Dates must follow the specified formats.
- Return ONLY JSON.
- Do NOT include markdown or explanations.
- Use empty string "" for missing fields, not null.

resume_text:
""" + pdf_text

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "you are a resume parser assistant and only gives result as output without specifying anything."},
                {"role": "user", "content": prmpt}
            ],
            temperature=0
        )

        result = response.choices[0].message.content
        
        # Clean markdown if present
        if result.startswith('```'):
            result = re.sub(r'^```json?\n?', '', result)
            result = re.sub(r'\n?```$', '', result)

        return result, 200

    except Exception as e:
        print(f"Error in /uploads: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'openai_key_set': bool(OPENAI_API_KEY)}), 200


if __name__ == '__main__':
    app.run(debug=True)