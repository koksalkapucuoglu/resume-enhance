import os
import re
import json
import secrets
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.forms import formset_factory
from django.shortcuts import redirect, render
from django.views.generic import TemplateView
from django.views.decorators.http import require_http_methods
from django.http import FileResponse, JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from PyPDF2 import PdfReader

from resume.forms import UserInfoForm, EducationForm, ExperienceForm, ProjectForm
from resume.openai_engine import (
    enhance_resume_experience,
    enhance_project_description,
    extract_resume_data,
    extract_linkedin_resume_data,
)
from latex_renderer import TexToPdfConverter, latex_handler


EducationFormSet = formset_factory(EducationForm, extra=0)
ExperienceFormSet = formset_factory(ExperienceForm, extra=0)
ProjectFormSet = formset_factory(ProjectForm, extra=0)


def get_init_values_for_resume_form():
    """
    This function return initial values for resume form as context dict.
    """
    user_form = UserInfoForm(
        initial={
            "full_name": "firstname lastname",
            "email": "firstname.lastname@gmail.com",
            "phone": "+1 123 456 7890",
            "skills": "Django, Flask, Fastapi",
        }
    )
    education_formset = EducationFormSet(
        initial=[
            {
                "school": "Stanford University",
                "degree": "Master",
                "field_of_study": "Computer Science",
                "start_date": "2024-01",
                "end_date": "2024-12",
            },
            {
                "school": "Stanford University",
                "degree": "Bachelor",
                "field_of_study": "Computer Science",
                "start_date": "2023-01",
                "end_date": "2023-12",
            },
        ],
        prefix="education",
    )
    experience_formset = ExperienceFormSet(
        initial=[
            {
                "title": "Role Name",
                "start_date": "2024-01",
                "end_date": "2024-10",
                "current_role": False,
                "company": "Company Last",
                "description": """Achieved X% growth for XYZ using A, B, and C skills.
    Led XYZ which led to X% improvement in ABC.
    Developed XYZ that did A, B and C using X, Y, and Z.""",
            },
            {
                "title": "Role Name",
                "start_date": "2023-01",
                "end_date": "2023-12",
                "current_role": False,
                "company": "Company First",
                "description": """Achieved X% growth for XYZ using A, B, and C skills.
            Led XYZ which led to X% improvement in ABC.
            Developed XYZ that did A, B and C using X, Y, and Z.""",
            },
        ],
        prefix="experience",
    )
    project_formset = ProjectFormSet(
        initial=[
            {
                "name": "Hiring Search Tool",
                "description": """Built a tool to search for Hiring Managers and Recruiters by using ReactJS, NodeJS, Firebase
    and boolean queries. Over 25000 people have used it so far, with 5000+ queries being saved and shared, and search
    results even better than LinkedIn.""",
                "link": None,
            },
            {
                "name": "Short Project Title.",
                "description": """Build a project that does something and had quantified success using A, B, and C. This
    project’s description spans two lines and also won an award.""",
                "link": None,
            },
            {
                "name": "Short Project Title.",
                "description": """Build a project that does something and had quantified success using A, B, and C. This
    project’s description spans two lines and also won an award.""",
                "link": None,
            },
        ],
        prefix="project",
    )

    context = {
        "user_form": user_form,
        "education_formset": education_formset,
        "experience_formset": experience_formset,
        "project_formset": project_formset,
    }
    return context


def populate_formsets_from_extracted_json(extracted_json):
    """
    Populates user_form, education_formset, experience_formset, and project_formset
    using the extracted JSON data.

    Args:
        extracted_json (dict): The JSON data extracted from the resume.

    Returns:
        dict: A dictionary containing the populated forms and formsets.
    """
    # User Info Form
    user_form = UserInfoForm(
        initial={
            "full_name": extracted_json.get("user_info", {}).get("full_name", ""),
            "email": extracted_json.get("user_info", {}).get("email", ""),
            "phone": extracted_json.get("user_info", {}).get("phone", ""),
            "github": extracted_json.get("user_info", {}).get("github", ""),
            "linkedin": extracted_json.get("user_info", {}).get("linkedin", ""),
            "skills": ", ".join(extracted_json.get("user_info", {}).get("skills", [])),
        }
    )

    # Education Formset
    education_initial = [
        {
            "school": edu.get("school", ""),
            "degree": edu.get("degree", ""),
            "field_of_study": edu.get("field_of_study", ""),
            "start_date": edu.get("start_date", ""),
            "end_date": edu.get("end_date", ""),
        }
        for edu in extracted_json.get("education", [])
    ]
    education_formset = EducationFormSet(initial=education_initial, prefix="education")

    # Experience Formset
    experience_initial = [
        {
            "title": exp.get("title", ""),
            "company": exp.get("company", ""),
            "start_date": exp.get("start_date", ""),
            "end_date": exp.get("end_date", ""),
            "current_role": exp.get("current_role", False),
            "description": exp.get("description", ""),
        }
        for exp in extracted_json.get("experience", [])
    ]
    experience_formset = ExperienceFormSet(
        initial=experience_initial, prefix="experience"
    )

    # Project Formset
    project_initial = [
        {
            "name": proj.get("name", ""),
            "description": proj.get("description", ""),
            "link": proj.get("link", ""),
        }
        for proj in extracted_json.get("projects_and_publications", [])
    ]
    project_formset = ProjectFormSet(initial=project_initial, prefix="project")

    return {
        "user_form": user_form,
        "education_formset": education_formset,
        "experience_formset": experience_formset,
        "project_formset": project_formset,
    }


class ResumeFormView(TemplateView):
    """
    Handle the display and processing of a resume form, including sections
    for user information, education, experience, and projects.

    If valid form submission,generate a LaTeX-based PDF resume and return it as a downloadable file.
    """

    template_name = "resume_form.html"

    def get(self, request, *args, **kwargs):
        education_formset = EducationFormSet(prefix="education")
        experience_formset = ExperienceFormSet(prefix="experience")
        project_formset = ProjectFormSet(prefix="project")
        user_form = UserInfoForm()

        # context = get_init_values_for_resume_form()
        context = {
            "user_form": user_form,
            "education_formset": education_formset,
            "experience_formset": experience_formset,
            "project_formset": project_formset,
        }
        extracted_json = request.session.pop("extracted_json", None)
        if extracted_json:
            context = populate_formsets_from_extracted_json(extracted_json)

        return self.render_to_response(context)

    @staticmethod
    def _split_experience_description(formset):
        """
        Splits and cleans the experience descriptions from the formset,
        removing empty lines.

        Args:
            formset: The formset containing experience data.

        Returns:
            list: A list of cleaned experience data with descriptions split
            into non-empty lines.
        """
        experience_data = list()
        for form in formset:
            if form.is_valid():
                cleaned_data = form.cleaned_data
                cleaned_data["description"] = list(
                    filter(
                        None,
                        map(str.strip, cleaned_data.get("description", "").split("\n")),
                    )
                )
                experience_data.append(cleaned_data)
        return experience_data

    def post(self, *args, **kwargs):
        """
        Handles the form submission for the resume, processes the data,
        and generates a LaTeX-based PDF resume.

        Returns:
            HttpResponse: The generated PDF file as a response,
            or renders the form again in case of validation failure.
        """
        user_form = UserInfoForm(self.request.POST)
        education_formset = EducationFormSet(self.request.POST, prefix="education")
        experience_formset = ExperienceFormSet(self.request.POST, prefix="experience")
        project_formset = ProjectFormSet(self.request.POST, prefix="project")

        if (
            user_form.is_valid()
            and education_formset.is_valid()
            and experience_formset.is_valid()
            and project_formset.is_valid()
        ):
            user_data = user_form.cleaned_data
            education_data = [
                form.cleaned_data for form in education_formset if form.cleaned_data
            ]

            experience_data = self._split_experience_description(experience_formset)
            project_data = [
                form.cleaned_data for form in project_formset if form.cleaned_data
            ]

            base_tex_template = self.request.POST.get(
                "base_tex_template", settings.LATEX_SETTINGS["DEFAULT_TEMPLATE"]
            )
            output_tex = (
                settings.LATEX_SETTINGS["TEMP_DIR"]
                / f"resume_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.tex"
            )
            tex_context = {
                "user_data": user_data,
                "education_data": education_data,
                "experience_data": experience_data,
                "project_data": project_data,
                "generation_date": datetime.now().strftime("%Y-%m-%d"),
            }

            try:
                tex_file = latex_handler.render_tex_template(
                    template_name=base_tex_template,
                    context=tex_context,
                    output_path=output_tex,
                )

                pdf_file_path = TexToPdfConverter(tex_file).render_pdf()

                response = FileResponse(
                    open(pdf_file_path, "rb"), content_type="application/pdf"
                )
                response["Content-Disposition"] = 'inline; filename="resume.pdf"'

                os.remove(pdf_file_path)
                return response
            except Exception as e:
                messages.error(self.request, f"Failed to render PDF: {str(e)}")
                return redirect("resume:resume_form")

        context = {
            "user_form": user_form,
            "education_formset": education_formset,
            "experience_formset": experience_formset,
            "project_formset": project_formset,
        }
        return self.render_to_response(context)


def get_field_value(request, prefix, field):
    """
    Extracts the index and value of a specific field from a POST request based on a
    dynamically generated field ID pattern.

    Args:
        request: The HTTP POST request object containing form data.
        prefix: The prefix used in the form field name (e.g., "education").
        field: The specific field name to locate (e.g., "field_of_study").

    Returns:
        A tuple containing:
        - form_index (str): The extracted index of the form.
        - field_value (str): The value associated with the specified field.
    """
    form_index = None
    field_value = None
    field_id = request.POST.get("field_id")

    if field_id:
        form_index_match = re.search(rf"id_{prefix}-(\d+)-{field}", field_id)
        form_index = form_index_match.group(1)
        field_value = request.POST.get(request.POST.get("field_id").strip("id_"))

    return form_index, field_value


def enhance_field(request, prefix, field, enhance_function):
    """
    Enhances the specified field based on given enhancement function.

    Args:
        request: The HTTP request object.
        prefix (str): Prefix to identify the formset (e.g., 'experience', 'project').
        field (str): Field name to enhance (e.g., 'description').
        enhance_function (func): Function that performs enhancement on the field's text.

    Returns:
        HttpResponse: Rendered HTML or error message.
    """
    form_index, field_value = get_field_value(request, prefix=prefix, field=field)
    if form_index and field_value:
        enhanced_text = enhance_function(field_value)
        description_html = f"""
        <textarea name="{prefix}-{form_index}-{field}" cols="40" rows="10" 
        class="textarea form-control" id="id_{prefix}-{form_index}-{field}">{enhanced_text}</textarea>
        """
        return HttpResponse(description_html)

    return HttpResponse({"error": "Invalid request"}, status=400)


@require_http_methods(["POST"])
def enhance_experience(request):
    return enhance_field(
        request,
        prefix="experience",
        field="description",
        enhance_function=enhance_resume_experience,
    )


@require_http_methods(["POST"])
def enhance_project(request):
    return enhance_field(
        request,
        prefix="project",
        field="description",
        enhance_function=enhance_project_description,
    )


@require_http_methods(["POST"])
def preview_resume_form(request):
    """
    Handles the preview of a resume form by rendering
    an HTML preview of the resume data.

    Args:
        request: The HTTP request object.

    Returns:
        JsonResponse: A JSON response containing the rendered HTML preview
        or an error message.
    """

    def split_experience_description(formset):
        """
        Splits and cleans the experience descriptions from the formset,
        removing empty lines.

        Args:
            formset: The formset containing experience data.

        Returns:
            list: A list of cleaned experience data with descriptions split
            into non-empty lines.
        """
        experience_data = list()
        for form in formset:
            if form.is_valid():
                cleaned_data = form.cleaned_data
                cleaned_data["description"] = list(
                    filter(
                        None,
                        map(str.strip, cleaned_data.get("description", "").split("\n")),
                    )
                )
                experience_data.append(cleaned_data)
        return experience_data

    if request.method == "POST":
        user_form = UserInfoForm(request.POST)
        education_formset = EducationFormSet(request.POST, prefix="education")
        experience_formset = ExperienceFormSet(request.POST, prefix="experience")
        project_formset = ProjectFormSet(request.POST, prefix="project")

        if (
            user_form.is_valid()
            and education_formset.is_valid()
            and experience_formset.is_valid()
            and project_formset.is_valid()
        ):
            user_data = user_form.cleaned_data
            education_data = [
                form.cleaned_data for form in education_formset if form.cleaned_data
            ]
            experience_data = split_experience_description(experience_formset)
            project_data = [
                form.cleaned_data for form in project_formset if form.cleaned_data
            ]

            context = {
                "user_data": user_data,
                "education_data": education_data,
                "experience_data": experience_data,
                "project_data": project_data,
                "generation_date": datetime.now().strftime("%Y-%m-%d"),
            }
            template_name = settings.TEX_PREVIEW_HTML_MAP.get(
                settings.LATEX_SETTINGS["DEFAULT_TEMPLATE"], None
            )
            if not template_name:
                return JsonResponse(
                    {"error": "Selected tex does not map any html preview"}, status=400
                )

            rendered_html = render(
                request, template_name=template_name, context=context
            ).content.decode("utf-8")
            return JsonResponse({"html": rendered_html})
    return JsonResponse({"error": "Invalid request"}, status=400)


def index(request):
    return render(request, "index.html")


@csrf_exempt
def upload_cv(request):
    if request.method == "POST":
        cv_file = request.FILES.get("cv_file")
        print("[INFO]: CV file uploaded:", cv_file.name)

        # Ensure the uploaded file is a PDF
        if not cv_file.name.endswith(".pdf"):
            return JsonResponse({"error": "Only PDF files are allowed."}, status=400)

        # Extract data from the PDF
        reader = PdfReader(cv_file)

        extracted_text = " ".join(page.extract_text() for page in reader.pages)
        import time

        start_time = time.time()
        extracted_json_string = extract_resume_data(extracted_text)
        end_time = time.time()
        print(f"OpenAI API response time: {end_time - start_time} seconds")

        if extracted_json_string.startswith("```json"):
            extracted_json_string = extracted_json_string[7:-3].strip()

        try:
            extracted_json = json.loads(extracted_json_string)
            request.session["extracted_json"] = extracted_json
        except json.JSONDecodeError as e:
            print("[ERROR]: Failed to decode JSON:", str(e))
            return JsonResponse({"error": "Failed to parse extracted JSON"}, status=500)

        return redirect("resume:resume_form")

    return redirect("resume:index")


def upload_linkedin(request):
    if request.method == "POST" and request.FILES.get("linkedin_file"):
        linkedin_file = request.FILES["linkedin_file"]
        print("[INFO]: CV file uploaded:", linkedin_file.name)

        # Ensure the uploaded file is a PDF
        if not linkedin_file.name.endswith(".pdf"):
            return JsonResponse({"error": "Only PDF files are allowed."}, status=400)

        # Extract data from the PDF
        reader = PdfReader(linkedin_file)

        extracted_text = " ".join(page.extract_text() for page in reader.pages)
        import time

        start_time = time.time()
        extracted_json_string = extract_linkedin_resume_data(extracted_text)
        end_time = time.time()
        print(f"OpenAI API response time: {end_time - start_time} seconds")

        if extracted_json_string.startswith("```json"):
            extracted_json_string = extracted_json_string[7:-3].strip()

        try:
            extracted_json = json.loads(extracted_json_string)
            request.session["extracted_json"] = extracted_json
        except json.JSONDecodeError as e:
            print("[ERROR]: Failed to decode JSON:", str(e))
            return JsonResponse({"error": "Failed to parse extracted JSON"}, status=500)

        return redirect("resume:resume_form")

    return redirect("resume:index")
