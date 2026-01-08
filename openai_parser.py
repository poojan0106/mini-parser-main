from flask import Flask, request, jsonify
from io import BytesIO
import fitz
import re
from openai import OpenAI
import os
import base64
import docx2txt
    
app = Flask(__name__)

OpenAI.api_key = os.getenv("OPENAI_API_KEY")
print("OpenAI.api_key",OpenAI.api_key)

def extract_text_from_doc(blob):
    try:
        text = docx2txt.process(BytesIO(blob))
        
        doc_str = re.sub(r'\s[,.]', ',', text)
        doc_str = re.sub(r'[\n]+', '\n', doc_str)
        doc_str = re.sub(r'[\s]+', ' ', doc_str)
        doc_str = re.sub(r'http[s]?(://)?', '', doc_str)
        doc_str = re.sub(r'[^\x00-\x7F]+', '', doc_str)
        return doc_str
      
    except Exception as e:
        print("An error occurred --1:", e)
        import traceback
        traceback.print_exc()
        return None  # Return None in case of an error

def extract_text_from_pdf(blob):
    try:
        pdf_data = fitz.open(stream=blob, filetype="pdf")
        all_text = ''
        for page_num in range(pdf_data.page_count):
            page = pdf_data.load_page(page_num)
            text = page.get_text("text")
            all_text += text + '\n\n'  # Separating text of different pages

        pdf_data.close()

        # Perform text cleaning
        pdf_str = re.sub(r'\s[,.]', ',', all_text)
        pdf_str = re.sub(r'[\n]+', '\n', pdf_str)
        pdf_str = re.sub(r'[\s]+', ' ', pdf_str)
        pdf_str = re.sub(r'http[s]?(://)?', '', pdf_str)
        pdf_str = re.sub(r'[^\x00-\x7F]+', '', pdf_str)
        return pdf_str

    except Exception as e:
        print("An error occurred:", e)
        return None  # Return None in case of an error

def detect_extension_from_blob(blob,file_extension):       
    try:
        if file_extension=='pdf':
            return extract_text_from_pdf(blob)
        elif file_extension=='docx':
            return extract_text_from_doc(blob)
        else:
            return 'unexpected file type'
        
    except Exception as e:
        print("An error occurred:", e)
        return None  # Return None in case of an error
     
     
@app.route('/upload', methods=['POST'])
def upload_blob():
    if request.method == 'POST':
        if 'application/json' in request.headers.get('Content-Type'):
            try:
                data = request.get_json()
            # Access the 'type' and 'encoded_blob' fields from the JSON data
                file_extension = data.get('type')
                encoded_blob = data.get('encoded_blob')
                # Decode Base64 to retrieve the Blob data
                blob_data = base64.b64decode(encoded_blob)
            except Exception as e:
                return str(e)
        else:
            return 'Invalid content type'
    else:
        return 'mathod not found',404
    
    pdf_text = detect_extension_from_blob(blob_data,file_extension)

    if pdf_text:
        pass
    else:
        print("Text extraction failed.")

    # API key should be set via environment variable OPENAI_API_KEY

    prmpt = """you will be provided with resume text and your task is to parse resume details very precisely and generate output in json format like this.\n{
    "PersonalInformation":{"Name":"","Email":"","Phone":"","Address":"","Location":""},
    "Skills":[]
    } \n\nresume_text:\n""" + pdf_text

    client = OpenAI()

    response = client.chat.completions.create(
      model="gpt-3.5-turbo-1106",
      messages=[
    {"role": "system", "content": "you are a resume parser assistant and only gives result as output without specifying anything."},
    {"role": "user", "content": prmpt}
     ]
    )

    return response.choices[0].message.content,200

if __name__ == '__main__':
    app.run(debug=true)

