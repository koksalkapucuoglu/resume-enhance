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
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from datetime import date, datetime


from PyPDF2 import PdfReader

from resume.forms import (
    UserInfoForm, EducationForm, ExperienceForm, ProjectForm
)
from resume.openai_engine import (
    enhance_resume_experience,
    enhance_project_description,
    extract_resume_data,
    extract_linkedin_resume_data,
)
from resume.services.pdf_service import ResumePdfService, PdfGenerationError
from resume.services.pdf_service import ResumePdfService, PdfGenerationError
from latex_renderer import latex_handler
from resume.models import Resume



EducationFormSet = formset_factory(EducationForm, extra=0)
ExperienceFormSet = formset_factory(ExperienceForm, extra=0)
ProjectFormSet = formset_factory(ProjectForm, extra=0)

from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin


def landing_page(request):
    """Landing page for anonymous and logged-in users."""
    if request.user.is_authenticated:
        return redirect('resume:dashboard')
    return render(request, "index.html")

@login_required
def selection_page(request):
    """Resume creation method selection page."""
    return render(request, "selection_page.html")


class DashboardView(LoginRequiredMixin, ListView):
    model = Resume
    template_name = "resume/dashboard.html"
    context_object_name = "resumes"
    ordering = ["-updated_at"]

    def get_queryset(self):
        return Resume.objects.filter(user=self.request.user).order_by("-updated_at")


@login_required
@require_http_methods(["POST"])
def duplicate_resume(request, pk):
    """
    Duplicates an existing resume for the current user.
    Creates a new resume with the same content but a new title.
    """
    try:
        original_resume = Resume.objects.get(pk=pk, user=request.user)
        
        # Create a copy with modified title
        new_resume = Resume.objects.create(
            user=request.user,
            title=f"{original_resume.title} (Copy)",
            content=original_resume.content.copy()  # Deep copy the JSON content
        )
        
        messages.success(request, f"Resume duplicated successfully!")
        return redirect("resume:resume_form_edit", pk=new_resume.pk)
        
    except Resume.DoesNotExist:
        messages.error(request, "Resume not found.")
        return redirect("resume:dashboard")


@login_required
@require_http_methods(["POST"])
def delete_resume(request, pk):
    """
    Deletes a resume for the current user.
    Only allows deletion of resumes owned by the requesting user.
    """
    try:
        resume = Resume.objects.get(pk=pk, user=request.user)
        resume_name = resume.display_name
        resume.delete()
        
        messages.success(request, f"'{resume_name}' has been deleted successfully.")
        return redirect("resume:dashboard")
        
    except Resume.DoesNotExist:
        messages.error(request, "Resume not found or you don't have permission to delete it.")
        return redirect("resume:dashboard")

def get_init_values_for_resume_form():
    """
    This function return initial values for resume form as context dict.
    """
    user_form = UserInfoForm(
        initial={
            "full_name": "firstname lastname",
            "email": "firstname.lastname@gmail.com",
            "phone": "+1 123 456 7890",
            "github": "https://github.com/yourusername",
            "linkedin": "https://linkedin.com/in/yourprofile",
            "skills": "Django, Flask, Fastapi",
        }
    )
    education_formset = EducationFormSet(
        initial=[
            {
                "school": "Stanford University",
                "degree": "Master",
                "field_of_study": "Computer Science",
                "start_year": 2024,
                "end_year": 2025,
            },
            {
                "school": "Stanford University",
                "degree": "Bachelor",
                "field_of_study": "Computer Science",
                "start_year": 2020,
                "end_year": 2024,
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
                "link": "http://localhost:8000/resume/form",
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
    def extract_year(date_value):
        """Extract year from various date formats (2020, '2020', '2020-05', etc.)"""
        if not date_value:
            return None
        if isinstance(date_value, int):
            return date_value
        if isinstance(date_value, str):
            # Handle formats like "2020-05", "2020", etc.
            year_str = date_value.split('-')[0] if '-' in date_value else date_value
            try:
                return int(year_str)
            except (ValueError, TypeError):
                return None
        return None
    
    education_initial = [
        {
            "school": edu.get("school", ""),
            "degree": edu.get("degree", ""),
            "field_of_study": edu.get("field_of_study", ""),
            "start_year": extract_year(edu.get("start_date") or edu.get("start_year")),
            "end_year": extract_year(edu.get("end_date") or edu.get("end_year")),
        }
        for edu in extracted_json.get("education", [])
    ]
    education_formset = EducationFormSet(initial=education_initial, prefix="education")

    # Experience Formset
    experience_initial = []
    for exp in extracted_json.get("experience", []):
        # Convert description array to newline-separated string for textarea
        desc = exp.get("description", "")
        if isinstance(desc, list):
            desc = "\n\n".join(desc)  # Double newline for visual spacing
        
        experience_initial.append({
            "title": exp.get("title", ""),
            "company": exp.get("company", ""),
            "start_date": exp.get("start_date", ""),
            "end_date": exp.get("end_date", ""),
            "current_role": exp.get("current_role", False),
            "description": desc,
        })
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

def clean_data_for_json(data):
    """
    Recursively converts date/datetime objects to strings for JSON serialization.
    """
    if isinstance(data, dict):
        return {k: clean_data_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_data_for_json(v) for v in data]
    elif isinstance(data, (date, datetime)):
        return data.strftime("%Y-%m")
    return data


class ResumeFormView(TemplateView):
    """
    Handle the display and processing of a resume form.
    Source: Database (Resume model)
    """
    template_name = "resume_form.html"

    def get_object(self):
        return Resume.objects.get(pk=self.kwargs.get("pk")) if self.kwargs.get("pk") else None

    def get(self, request, *args, **kwargs):
        resume = self.get_object()
        
        # If accessing form directly without PK, show empty form or redirect? 
        # For now, let's keep it empty or load from session if we want backward compatibility, 
        # but the plan is to drop session. Let's assume new flow always has PK.
        # If no PK, show init values.
        
        if resume:
            context = populate_formsets_from_extracted_json(resume.content)
            context["resume_id"] = resume.pk
        else:
            # Fallback for "Create New" flow without upload
            context = get_init_values_for_resume_form()

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
                cleaned_data = form.cleaned_data.copy()
                cleaned_data["description"] = list(
                    filter(
                        None,
                        map(str.strip, cleaned_data.get("description", "").split("\n")),
                    )
                )
                experience_data.append(cleaned_data)
        return experience_data

    
    def _prepare_resume_context(self, user_form, education_formset, experience_formset, project_formset):
        """
        Prepare context data for resume generation.
        
        Args:
            user_form: User information form
            education_formset: Education formset
            experience_formset: Experience formset  
            project_formset: Project formset
            
        Returns:
            dict: Context data for resume generation
        """
        user_data = user_form.cleaned_data
        education_data = [
            form.cleaned_data for form in education_formset if form.cleaned_data
        ]

        experience_data = self._split_experience_description(experience_formset)
        project_data = [
            form.cleaned_data for form in project_formset if form.cleaned_data
        ]

        return {
            "user_data": user_data,
            "education_data": education_data,
            "experience_data": experience_data,
            "project_data": project_data,
            "generation_date": datetime.now().strftime("%Y-%m-%d"),
        }

    def _generate_tex_file(self, context):
        """
        Generate TEX file using the existing LaTeX handler.
        
        Args:
            context: Resume data context
            
        Returns:
            HttpResponse: TEX file download response
        """
        base_tex_template = self.request.POST.get(
            "base_tex_template", settings.LATEX_SETTINGS["DEFAULT_TEMPLATE"]
        )
        output_tex = (
            settings.LATEX_SETTINGS["TEMP_DIR"]
            / f"resume_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.tex"
        )
        
        tex_file = latex_handler.render_tex_template(
            template_name=base_tex_template,
            context=context,
            output_path=output_tex,
        )
        
        with open(tex_file, 'rb') as f:
            tex_content = f.read()
        
        response = HttpResponse(tex_content, content_type='application/x-tex')
        response['Content-Disposition'] = 'attachment; filename="resume.tex"'
        return response

    def _generate_pdf_file(self, context):
        """
        Generate PDF file using WeasyPrint.
        
        Args:
            context: Resume data context
            
        Returns:
            HttpResponse: PDF file response
        """
        try:
            # Get template selector from request
            template_selector = self.request.POST.get('template_selector', 'faangpath-simple')
            
            resume_pdf_service = ResumePdfService()
            pdf_bytes = resume_pdf_service.generate_resume_pdf(
                resume_data=context,
                template_selector=template_selector,
                request=self.request
            )
            
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = 'inline; filename="resume.pdf"'
            return response
            
        except PdfGenerationError as e:
            messages.error(self.request, f"Failed to generate PDF: {str(e)}")
            raise

    def post(self, *args, **kwargs):
        """
        Handles the form submission for the resume, processes the data,
        and generates a HTML based PDF resume or LaTeX file.

        Returns:
            HttpResponse: The generated PDF file as a response,
            or renders the form again in case of validation failure.
        """
        user_form = UserInfoForm(self.request.POST)
        education_formset = EducationFormSet(self.request.POST, prefix="education")
        experience_formset = ExperienceFormSet(self.request.POST, prefix="experience")
        project_formset = ProjectFormSet(self.request.POST, prefix="project")

        # Validate all forms and formsets
        # If any form is invalid, re-render the form with errors
        # and do not proceed to PDF generation
        if not all([
            user_form.is_valid(),
            education_formset.is_valid(),
            experience_formset.is_valid(),
            project_formset.is_valid()
        ]):
            if not user_form.is_valid():
                messages.error(self.request, "User information section has errors.")
            if not education_formset.is_valid():
                messages.error(self.request, "Education section has errors.")
            if not experience_formset.is_valid():
                messages.error(self.request, "Experience section has errors.")
            if not project_formset.is_valid():
                messages.error(self.request, "Project section has errors.")

            context = {
                "user_form": user_form,
                "education_formset": education_formset,
                "experience_formset": experience_formset,
                "project_formset": project_formset,
            }
            return self.render_to_response(context)

        if self.kwargs.get("pk"):
            try:
                resume = Resume.objects.get(pk=self.kwargs.get("pk"))
                # Update resume content with clean data from forms
                # We need to reconstruct the content JSON structure
                updated_content = {
                    "user_info": clean_data_for_json({
                        "full_name": user_form.cleaned_data.get("full_name"),
                        "email": user_form.cleaned_data.get("email"),
                        "phone": user_form.cleaned_data.get("phone"),
                        "github": user_form.cleaned_data.get("github"),
                        "linkedin": user_form.cleaned_data.get("linkedin"),
                        "skills": [s.strip() for s in user_form.cleaned_data.get("skills", "").split(",")],
                    }),
                    "education": clean_data_for_json([f.cleaned_data for f in education_formset if f.cleaned_data]),
                    "experience": clean_data_for_json(self._split_experience_description(experience_formset)),
                    "projects_and_publications": clean_data_for_json([f.cleaned_data for f in project_formset if f.cleaned_data]),
                }
                resume.content = updated_content
                resume.save()
            except Resume.DoesNotExist:
                # Should not happen typically
                pass

        # Check if this is a save-only request (AJAX)
        if self.request.POST.get("form_action") == "save_only":
            return JsonResponse({"status": "saved", "message": "Resume saved successfully"})

        # Prepare resume context
        context = self._prepare_resume_context(
            user_form, education_formset, experience_formset, project_formset
        )
        
        # Check if this is a preview request for our new template
        if self.request.POST.get("action") == "preview_faangpath":
            return render(self.request, "faangpath_simple_template_pdf.html", context)
        
        try:
            # Determine export format
            export_format = self.request.POST.get("export_format", "pdf").lower()
            if export_format == "tex":
                return self._generate_tex_file(context)
            else:
                return self._generate_pdf_file(context)
            
        except Exception as e:
            messages.error(self.request, f"Failed to render PDF: {str(e)}")
            # If error, stay on the same page. If we have PK, we should probably redirect or just render.
            # Ideally redirect to avoid resubmission issues, but we need errors.
            # Render is fine for now.
            # Re-populate context for error page
            context = {
                "user_form": user_form,
                "education_formset": education_formset,
                "experience_formset": experience_formset,
                "project_formset": project_formset,
            }
            if self.kwargs.get("pk"):
                 context["resume_id"] = self.kwargs.get("pk")
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
        
        # Format with double newlines for better readability
        # If GPT returns single-newline separated bullets, convert to double
        if enhanced_text and '\n' in enhanced_text:
            lines = [line.strip() for line in enhanced_text.split('\n') if line.strip()]
            enhanced_text = '\n\n'.join(lines)
        
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
                cleaned_data = form.cleaned_data.copy()
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
            
            # Use the same template as PDF generation for consistency
            template_name = 'faangpath_simple_template_pdf.html'
            
            rendered_html = render(
                request, template_name=template_name, context=context
            ).content.decode("utf-8")
            return HttpResponse(rendered_html)
        
        errors = {}
        if user_form.errors:
            errors["user_form"] = user_form.errors
        education_errors = [form.errors for form in education_formset if form.errors]
        if education_errors:
            errors["education_formset"] = education_errors
        experience_errors = [form.errors for form in experience_formset if form.errors]
        if experience_errors:
            errors["experience_formset"] = experience_errors
        project_errors = [form.errors for form in project_formset if form.errors]
        if project_errors:
            errors["project_formset"] = project_errors

        return JsonResponse({"error": "Invalid request", "form_errors": errors}, status=400)


@login_required
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
        
        start_time = datetime.now()
        extracted_json_string = extract_resume_data(extracted_text)
        print(f"OpenAI API response time: {datetime.now() - start_time}")
        # print(f"[DEBUG] Raw OpenAI Response: {extracted_json_string}")

        # Check for API Errors
        if extracted_json_string.startswith("OpenAI API") or extracted_json_string.startswith("Error:"):
            return JsonResponse({"error": f"AI Service Error: {extracted_json_string}"}, status=503)

        # Clean up JSON string (remove markdown code blocks)
        if "```json" in extracted_json_string:
            extracted_json_string = extracted_json_string.split("```json")[1].split("```")[0].strip()
        elif "```" in extracted_json_string:
             extracted_json_string = extracted_json_string.split("```")[1].strip()
        
        extracted_json_string = extracted_json_string.strip()

        try:
            extracted_json = json.loads(extracted_json_string)
            
            # Save to Database instead of Session
            resume = Resume.objects.create(
                user=request.user,
                title=f"Uploaded Resume {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                content=extracted_json
            )
            
            # Redirect to form with resume ID
            return redirect("resume:resume_form_edit", pk=resume.pk)
            
        except json.JSONDecodeError as e:
            print("[ERROR]: Failed to decode JSON:", str(e))
            return JsonResponse({"error": "Failed to parse extracted JSON"}, status=500)

    return redirect("resume:index")


@login_required
def upload_linkedin_cv(request):
    if request.method == "POST" and request.FILES.get("linkedin_file"):
        linkedin_file = request.FILES["linkedin_file"]
        print("[INFO]: LinkedIn file uploaded:", linkedin_file.name)

        if not linkedin_file.name.endswith(".pdf"):
            return JsonResponse({"error": "Only PDF files are allowed."}, status=400)

        reader = PdfReader(linkedin_file)
        extracted_text = " ".join(page.extract_text() for page in reader.pages)
        
        start_time = datetime.now()
        extracted_json_string = extract_linkedin_resume_data(extracted_text)
        print(f"OpenAI API response time: {datetime.now() - start_time}")
        # print(f"[DEBUG] Raw OpenAI Response: {extracted_json_string}")

        # Check for API Errors
        if extracted_json_string.startswith("OpenAI API") or extracted_json_string.startswith("Error:"):
            return JsonResponse({"error": f"AI Service Error: {extracted_json_string}"}, status=503)

        # Clean up JSON string (remove markdown code blocks)
        if "```json" in extracted_json_string:
            extracted_json_string = extracted_json_string.split("```json")[1].split("```")[0].strip()
        elif "```" in extracted_json_string:
             extracted_json_string = extracted_json_string.split("```")[1].strip()
        
        extracted_json_string = extracted_json_string.strip()

        try:
            extracted_json = json.loads(extracted_json_string)
            
            # Save to Database
            resume = Resume.objects.create(
                user=request.user,
                title=f"LinkedIn Profile {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                content=extracted_json
            )
            
            return redirect("resume:resume_form_edit", pk=resume.pk)
            
        except json.JSONDecodeError as e:
            print("[ERROR]: Failed to decode JSON:", str(e))
            return JsonResponse({"error": "Failed to parse extracted JSON"}, status=500)

    return redirect("resume:index")


def test_faangpath_template(request):
    """
    Test view to render the faangpath_simple_template_pdf.html template
    with sample data for development and testing purposes.
    """
    if request.method == "POST":
        user_form = UserInfoForm(request.POST)
        education_formset = EducationFormSet(request.POST, prefix="education")
        experience_formset = ExperienceFormSet(request.POST, prefix="experience")
        project_formset = ProjectFormSet(request.POST, prefix="project")

        # Validate all forms and formsets
        if all([
            user_form.is_valid(),
            education_formset.is_valid(),
            experience_formset.is_valid(),
            project_formset.is_valid()
        ]):
            # Prepare context data for template
            user_data = user_form.cleaned_data
            education_data = [
                form.cleaned_data for form in education_formset if form.cleaned_data
            ]
            
            # Split experience descriptions into lists
            experience_data = []
            for form in experience_formset:
                if form.is_valid() and form.cleaned_data:
                    cleaned_data = form.cleaned_data.copy()
                    cleaned_data["description"] = list(
                        filter(
                            None,
                            map(str.strip, cleaned_data.get("description", "").split("\n")),
                        )
                    )
                    experience_data.append(cleaned_data)
            
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
            
            return render(request, "faangpath_simple_template_pdf.html", context)
        else:
            messages.error(request, "Please correct the errors in the form.")
    
    # If GET request or form validation failed, show the form
    context = get_init_values_for_resume_form()
    return render(request, "resume_form.html", context)
