# django-enhance-resume-app

This project is an AI-enhanced Resume application built using Django, which leverages Django formsets, OpenAI, and pdflatex for the backend. The workflow is as follows:

* **Form Data Entry**: Users fill out a form with information related to their resume, such as personal details, education, experience, etc.

* **AI Enhancement**: Before submitting the form, users have the ability to enhance specific fields instantly using AI. They can choose any entry of experience and project sections and improve it with AI suggestions, allowing for dynamic content refinement.

* **Preview Option**: After enhancing the desired sections, users can preview how the resume will look in its final format. This allows them to see the structure and layout of the resume before finalizing.

* **PDF Generation**: Once the user is satisfied with the form and AI enhancements, they can submit the form. The application then generates the resume in PDF format, which the user can download.

This solution ensures a flexible and interactive experience for the user, enabling them to refine their resume content dynamically and preview it before generating the final PDF version.

Currently, resumes can only be exported as [faangpath simple template](https://www.overleaf.com/latex/templates/faangpath-simple-template/npsfpdqnxmbc). Different resume templates will be added here.

## Tech Stack

**Core Tech:** Python

**Backend Service:** Django, OpenAI, PDFLatex

**IU Integrations**: Htmx, Jinja2

## Run Locally using Docker

Clone the project

```bash
  git clone https://github.com/koksalkapucuoglu/resume-enhance.git
```

Go to the project directory

```bash
  cd resume-enhance
```

Build

```bash
  make build
```

Setup .env

```bash
  mkdir .env
```
* add **OPENAI_API_KEY = 'api key'** to .env file

Run project

```bash
  make run
```

## Project Screenshots

Preview

![Preview](https://github.com/koksalkapucuoglu/resume-enhance/blob/main/screenshots/preview.png?raw=true)


Pdf Export

![Pdf Export](https://github.com/koksalkapucuoglu/resume-enhance/blob/main/screenshots/pdf_export.png?raw=true)


## Planned Features

- Extract resume information from an existing PDF and fill the form automatically.  
- Import resume details from LinkedIn and fill the form automatically.  
- Add support for multiple LaTeX templates.  
- Implement an authentication system.  
- Improve the user interface (UI).  
- Provide options for selecting different GPT models.  
- Add multi-language support.  
- Add a flexible section structure for resumes.   
