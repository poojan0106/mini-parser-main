from flask import Flask, request, jsonify
from io import BytesIO
import fitz  # PyMuPDF
import re
from openai import OpenAI
import os
import base64
import docx2txt
from striprtf.striprtf import rtf_to_text
import json
import time

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY environment variable not set")

PARSER_PROMPT = """You are an expert CV/Resume parser. Extract ALL information from this CV document.

IMPORTANT:
- Extract EVERY job position from work/professional experience section
- Extract EVERY education entry (degrees, universities)
- Do NOT skip any entries

Return ONLY a valid JSON object with this EXACT structure:
{
  "personalInfo": {
    "firstName": "string",
    "lastName": "string",
    "email": "string",
    "phone": "string",
    "mobile": "string",
    "linkedIn": "string",
    "address": { "street": "string", "city": "string", "state": "string", "postalCode": "string", "country": "string" }
  },
  "workHistory": [
    {
      "company": "company name",
      "title": "job title",
      "startDate": "YYYY-MM",
      "endDate": "YYYY-MM or Present",
      "description": "key responsibilities"
    }
  ],
  "education": [
    {
      "institution": "university name",
      "degree": "degree name",
      "major": "field of study",
      "graduationYear": "YYYY"
    }
  ],
  "skills": ["skill1", "skill2"],
  "certifications": ["cert1", "cert2"],
  "summary": "2-3 sentence professional summary",
  "totalYearsExperience": 0
}

RULES:
1. Extract ALL jobs - this CV should have multiple positions
2. Extract ALL education - look for degrees, universities, graduation years
3. Convert month names to numbers (June=06, August=08)
4. Use "" for missing strings, [] for missing arrays
5. Return ONLY JSON - no markdown, no explanation"""


def extract_text_from_pdf(blob):
    """Extract text from PDF file"""
    try:
        pdf_data = fitz.open(stream=blob, filetype='pdf')
        all_text = ''
        for page_num in range(pdf_data.page_count):
            page = pdf_data.load_page(page_num)
            text = page.get_text("text")
            all_text += text + '\n\n'
        pdf_data.close()

        if not all_text.strip():
            return None

        return clean_text(all_text)
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return None


def extract_text_from_docx(blob):
    """Extract text from DOCX file"""
    try:
        text = docx2txt.process(BytesIO(blob))
        if not text:
            return None
        return clean_text(text)
    except Exception as e:
        print(f"DOCX extraction error: {e}")
        return None


def extract_text_from_txt(blob):
    """Extract text from plain text file"""
    try:
        text = blob.decode('utf-8', errors='ignore')
        if not text.strip():
            return None
        return clean_text(text)
    except Exception as e:
        print(f"TXT extraction error: {e}")
        return None


def extract_text_from_rtf(blob):
    """Extract text from RTF file"""
    try:
        rtf_content = blob.decode('utf-8', errors='ignore')
        text = rtf_to_text(rtf_content)
        if not text.strip():
            return None
        return clean_text(text)
    except Exception as e:
        print(f"RTF extraction error: {e}")
        return None


def clean_text(text):
    """Clean and normalize extracted text"""
    text = re.sub(r'\s[,.]', ',', text)
    text = re.sub(r'[\n]+', '\n', text)
    text = re.sub(r'[\s]+', ' ', text)
    text = re.sub(r'http[s]?(://)?', '', text)
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    return text.strip()


def detect_and_extract(blob, file_extension):
    """Detect file type and extract text"""
    ext = file_extension.lower().strip().lstrip('.')

    extractors = {
        'pdf': extract_text_from_pdf,
        'docx': extract_text_from_docx,
        'doc': extract_text_from_docx,
        'txt': extract_text_from_txt,
        'text': extract_text_from_txt,
        'rtf': extract_text_from_rtf
    }

    extractor = extractors.get(ext)
    if extractor:
        return extractor(blob)
    else:
        print(f"Unsupported file type: {ext}")
        return None


def get_empty_response():
    """Return empty response structure"""
    return {
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
    }


def parse_with_assistant(file_blob, file_extension):
    """Parse resume using OpenAI Assistants API"""
    client = OpenAI(api_key=OPENAI_API_KEY)

    # Determine MIME type
    mime_types = {
        'pdf': 'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'doc': 'application/msword',
        'txt': 'text/plain',
        'text': 'text/plain',
        'rtf': 'application/rtf'
    }

    ext = file_extension.lower().strip().lstrip('.')
    mime_type = mime_types.get(ext, 'application/octet-stream')
    filename = f"resume.{ext}"

    # Upload file to OpenAI
    file = client.files.create(
        file=(filename, BytesIO(file_blob), mime_type),
        purpose='assistants'
    )

    # Create assistant
    assistant = client.beta.assistants.create(
        name="Resume Parser",
        instructions=PARSER_PROMPT,
        model="gpt-4o",
        tools=[{"type": "file_search"}]
    )

    # Create vector store and add file
    vector_store = client.beta.vector_stores.create(name="Resume Store")

    client.beta.vector_stores.files.create(
        vector_store_id=vector_store.id,
        file_id=file.id
    )

    # Wait for file processing
    while True:
        vs_file = client.beta.vector_stores.files.retrieve(
            vector_store_id=vector_store.id,
            file_id=file.id
        )
        if vs_file.status == 'completed':
            break
        elif vs_file.status == 'failed':
            raise Exception("File processing failed")
        time.sleep(1)

    # Update assistant with vector store
    client.beta.assistants.update(
        assistant_id=assistant.id,
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}}
    )

    # Create thread and run
    thread = client.beta.threads.create()

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content="Parse this resume and extract all information. Return ONLY valid JSON."
    )

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id
    )

    # Wait for completion
    while True:
        run_status = client.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id
        )
        if run_status.status == 'completed':
            break
        elif run_status.status in ['failed', 'cancelled', 'expired']:
            raise Exception(f"Run failed with status: {run_status.status}")
        time.sleep(1)

    # Get response
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    assistant_message = None
    for msg in messages.data:
        if msg.role == 'assistant':
            assistant_message = msg
            break

    if not assistant_message:
        raise Exception("No response from assistant")

    result = assistant_message.content[0].text.value

    # Cleanup
    client.beta.assistants.delete(assistant.id)
    client.beta.vector_stores.delete(vector_store.id)
    client.files.delete(file.id)

    return result


def parse_with_chat(text):
    """Fallback: Parse resume using Chat Completions API"""
    client = OpenAI(api_key=OPENAI_API_KEY)

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": PARSER_PROMPT},
            {"role": "user", "content": f"Parse this resume:\n\n{text}"}
        ],
        temperature=0
    )

    return response.choices[0].message.content


def clean_json_response(result):
    """Clean markdown from JSON response"""
    if result.startswith('```'):
        result = re.sub(r'^```json?\n?', '', result)
        result = re.sub(r'\n?```$', '', result)
    return result.strip()


@app.route('/parse', methods=['POST'])
def parse_resume():
    """Parse resume endpoint - uses Assistants API"""
    if 'application/json' not in request.headers.get('Content-Type', ''):
        return jsonify({'error': 'Invalid content type. Expected application/json'}), 400

    try:
        data = request.get_json()
        file_extension = data.get('type')
        encoded_blob = data.get('encoded_blob')

        if not file_extension:
            return jsonify({'error': 'Missing "type" field'}), 400
        if not encoded_blob:
            return jsonify({'error': 'Missing "encoded_blob" field'}), 400

        try:
            blob_data = base64.b64decode(encoded_blob)
        except Exception as e:
            return jsonify({'error': f'Invalid base64 data: {str(e)}'}), 400

        # Parse using Assistants API
        result = parse_with_assistant(blob_data, file_extension)
        result = clean_json_response(result)

        # Validate JSON
        try:
            parsed = json.loads(result)
            return jsonify(parsed), 200
        except json.JSONDecodeError:
            return result, 200

    except Exception as e:
        print(f"Error in /parse: {e}")
        return jsonify({'error': str(e), **get_empty_response()}), 500


@app.route('/parse-text', methods=['POST'])
def parse_resume_text():
    """Parse resume endpoint - extracts text first, then uses Chat API"""
    if 'application/json' not in request.headers.get('Content-Type', ''):
        return jsonify({'error': 'Invalid content type. Expected application/json'}), 400

    try:
        data = request.get_json()
        file_extension = data.get('type')
        encoded_blob = data.get('encoded_blob')

        if not file_extension:
            return jsonify({'error': 'Missing "type" field'}), 400
        if not encoded_blob:
            return jsonify({'error': 'Missing "encoded_blob" field'}), 400

        try:
            blob_data = base64.b64decode(encoded_blob)
        except Exception as e:
            return jsonify({'error': f'Invalid base64 data: {str(e)}'}), 400

        # Extract text
        text = detect_and_extract(blob_data, file_extension)

        if not text:
            return jsonify({
                'error': 'Text extraction failed. File may be corrupted or unsupported.',
                **get_empty_response()
            }), 400

        # Parse using Chat API
        result = parse_with_chat(text)
        result = clean_json_response(result)

        # Validate JSON
        try:
            parsed = json.loads(result)
            return jsonify(parsed), 200
        except json.JSONDecodeError:
            return result, 200

    except Exception as e:
        print(f"Error in /parse-text: {e}")
        return jsonify({'error': str(e), **get_empty_response()}), 500


@app.route('/upload', methods=['POST'])
def upload_blob():
    """Legacy upload endpoint - simple parsing with gpt-3.5-turbo"""
    if request.method == 'POST':
        if 'application/json' in request.headers.get('Content-Type', ''):
            try:
                data = request.get_json()
                file_extension = data.get('type')
                encoded_blob = data.get('encoded_blob')
                blob_data = base64.b64decode(encoded_blob)
            except Exception as e:
                return str(e), 400
        else:
            return 'Invalid content type', 400
    else:
        return 'Method not found', 404

    pdf_text = detect_and_extract(blob_data, file_extension)

    if not pdf_text:
        return jsonify({'error': 'Text extraction failed.'}), 400

    client = OpenAI(api_key=OPENAI_API_KEY)

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


@app.route('/uploads', methods=['POST'])
def upload_blobParser():
    """Legacy uploads endpoint - detailed parsing with gpt-4o"""
    if 'application/json' not in request.headers.get('Content-Type', ''):
        return jsonify({'error': 'Invalid content type. Expected application/json'}), 400

    try:
        data = request.get_json()
        file_extension = data.get('type')
        encoded_blob = data.get('encoded_blob')

        if not file_extension:
            return jsonify({'error': 'Missing "type" field'}), 400
        if not encoded_blob:
            return jsonify({'error': 'Missing "encoded_blob" field'}), 400

        try:
            blob_data = base64.b64decode(encoded_blob)
        except Exception as e:
            return jsonify({'error': f'Invalid base64 data: {str(e)}'}), 400

        pdf_text = detect_and_extract(blob_data, file_extension)

        if not pdf_text:
            return jsonify({
                'error': 'Text extraction failed.',
                **get_empty_response()
            }), 400

        client = OpenAI(api_key=OPENAI_API_KEY)

        result = parse_with_chat(pdf_text)
        result = clean_json_response(result)

        try:
            parsed = json.loads(result)
            return jsonify(parsed), 200
        except json.JSONDecodeError:
            return result, 200

    except Exception as e:
        print(f"Error in /uploads: {e}")
        return jsonify({'error': str(e), **get_empty_response()}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'openai_key_set': bool(OPENAI_API_KEY),
        'supported_formats': ['pdf', 'docx', 'doc', 'txt', 'rtf']
    }), 200


if __name__ == '__main__':
    app.run(debug=True)
