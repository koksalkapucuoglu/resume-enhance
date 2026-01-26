#!/usr/bin/env python3
"""
Simple test script to verify the resume download functionality.
This script will make HTTP requests to test both PDF and TEX downloads.
"""

import requests
import json

BASE_URL = "http://localhost:8000"

def test_downloads():
    """Test both PDF and TEX download functionality"""
    
    # Test data for the form
    form_data = {
        'csrfmiddlewaretoken': '',  # We'll get this from the form
        
        # User info
        'full_name': 'John Doe',
        'email': 'john.doe@example.com',
        'phone': '+1234567890',
        'linkedin': 'https://linkedin.com/in/johndoe',
        'github': 'https://github.com/johndoe',
        'skills': 'Python, Django, JavaScript',
        
        # Education formset
        'education-TOTAL_FORMS': '1',
        'education-INITIAL_FORMS': '0',
        'education-MIN_NUM_FORMS': '0',
        'education-MAX_NUM_FORMS': '1000',
        
        'education-0-school': 'Test University',
        'education-0-degree': 'Bachelor',
        'education-0-field_of_study': 'Computer Science',
        'education-0-start_date': '2020-01',
        'education-0-end_date': '2024-05',
        
        # Experience formset
        'experience-TOTAL_FORMS': '1',
        'experience-INITIAL_FORMS': '0',
        'experience-MIN_NUM_FORMS': '0',
        'experience-MAX_NUM_FORMS': '1000',
        
        'experience-0-title': 'Software Engineer',
        'experience-0-company': 'Test Company',
        'experience-0-start_date': '2024-06',
        'experience-0-end_date': '2025-06',
        'experience-0-current_role': False,
        'experience-0-description': 'Test description of work experience.',
        
        # Project formset
        'project-TOTAL_FORMS': '1',
        'project-INITIAL_FORMS': '0',
        'project-MIN_NUM_FORMS': '0',
        'project-MAX_NUM_FORMS': '1000',
        
        'project-0-name': 'Test Project',
        'project-0-description': 'Test project description.',
        'project-0-link': 'https://github.com/johndoe/test-project',
    }
    
    session = requests.Session()
    
    try:
        # First, get the form to extract the CSRF token
        print("Getting form page...")
        form_response = session.get(f"{BASE_URL}/resume/form/")
        form_response.raise_for_status()
        
        # Extract CSRF token (simple approach)
        csrf_start = form_response.text.find('name="csrfmiddlewaretoken" value="')
        if csrf_start != -1:
            csrf_start += len('name="csrfmiddlewaretoken" value="')
            csrf_end = form_response.text.find('"', csrf_start)
            csrf_token = form_response.text[csrf_start:csrf_end]
            form_data['csrfmiddlewaretoken'] = csrf_token
            print(f"CSRF Token: {csrf_token[:10]}...")
        else:
            print("Could not find CSRF token")
            return
        
        # Test TEX download
        print("\nTesting TEX download...")
        tex_data = form_data.copy()
        tex_data['export_format'] = 'tex'
        
        tex_response = session.post(f"{BASE_URL}/resume/form/", data=tex_data)
        print(f"TEX Response Status: {tex_response.status_code}")
        print(f"TEX Content Type: {tex_response.headers.get('content-type', 'unknown')}")
        print(f"TEX Content Length: {len(tex_response.content)} bytes")
        
        if tex_response.status_code == 200:
            print("✅ TEX download successful!")
            # Save a sample of the content
            with open('/tmp/test_resume.tex', 'wb') as f:
                f.write(tex_response.content)
            print("TEX file saved to /tmp/test_resume.tex")
        else:
            print("❌ TEX download failed!")
            print(f"Response content: {tex_response.text[:500]}...")
        
        # Test PDF download (which should now return HTML)
        print("\nTesting PDF download (now returns HTML)...")
        pdf_data = form_data.copy()
        pdf_data['export_format'] = 'pdf'
        
        pdf_response = session.post(f"{BASE_URL}/resume/form/", data=pdf_data)
        print(f"PDF Response Status: {pdf_response.status_code}")
        print(f"PDF Content Type: {pdf_response.headers.get('content-type', 'unknown')}")
        print(f"PDF Content Length: {len(pdf_response.content)} bytes")
        
        if pdf_response.status_code == 200:
            print("✅ PDF download successful!")
            # Save a sample of the content
            with open('/tmp/test_resume.html', 'wb') as f:
                f.write(pdf_response.content)
            print("HTML file saved to /tmp/test_resume.html")
        else:
            print("❌ PDF download failed!")
            print(f"Response content: {pdf_response.text[:500]}...")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    test_downloads()
