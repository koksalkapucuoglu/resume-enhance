import os
import re
from datetime import datetime


from django.conf import settings
from django.contrib import messages
from django.forms import formset_factory
from django.shortcuts import redirect, render
from django.views.generic import TemplateView
from django.views.decorators.http import require_http_methods
from django.http import FileResponse, JsonResponse, HttpResponse

from resume.forms import (
    UserInfoForm, EducationForm, ExperienceForm, ProjectForm, TestForm
)
from resume.openai_engine import (
    enhance_resume_experience, enhance_project_description
)
from latex_renderer import  TexToPdfConverter, latex_handler


EducationFormSet = formset_factory(EducationForm, extra=0)
ExperienceFormSet = formset_factory(ExperienceForm, extra=0)
ProjectFormSet = formset_factory(ProjectForm, extra=0)
TestFormSet = formset_factory(TestForm, extra=0)


class ResumeFormView(TemplateView):
    """
    Handle the display and processing of a resume form, including sections
    for user information, education, experience, and projects.

    Upon valid form submission,
    generate a LaTeX-based PDF resume and return it as a downloadable file.
    """
    template_name = "resume_form.html"

    def get(self, *args, **kwargs):
        """education_formset = EducationFormSet(prefix='education')
        experience_formset = ExperienceFormSet(prefix='experience')
        project_formset = ProjectFormSet(prefix='project')
        user_form = UserInfoForm()"""

        user_form = UserInfoForm(
            initial={
                'full_name': 'Köksal Kapucuoğlu',
                'email': 'koksalkapucuoglu@gmail.com',
                'phone': '+90 531 675 42 15',
                'skills': 'Django, Flask, Fastapi',
            }
        )
        education_formset = EducationFormSet(
            initial=[
                {
                    'school': 'Marmara',
                    'degree': 'Master',
                    'field_of_study': 'Electronic',
                    'start_date': '2023-01',
                    'end_date': '2023-05',
                }
            ],
            prefix='education'
        )
        experience_formset = ExperienceFormSet(
            initial=[
                {
                    'title': 'Developer',
                    'start_date': '2023-01',
                    'end_date': '2023-05',
                    'current_role': False,
                    'company': 'GDA',
                    'description': 'Implemented Backend Api',
                }
            ],
            prefix='experience'
        )
        project_formset = ProjectFormSet(
            initial=[
                {
                    'name': 'Project',
                    'description': 'LLL implemented',
                    'link': None,
                }
            ],
            prefix='project'
        )

        context = {
            'user_form': user_form,
            'education_formset': education_formset,
            'experience_formset': experience_formset,
            'project_formset': project_formset,
        }
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
                cleaned_data['description'] = list(
                    filter(None, map(
                            str.strip, cleaned_data.get('description', '').split('\n')
                        )
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
        education_formset = EducationFormSet(
            self.request.POST, prefix='education')
        experience_formset = ExperienceFormSet(
            self.request.POST, prefix='experience')
        project_formset = ProjectFormSet(
            self.request.POST, prefix='project')

        if (
                user_form.is_valid()
                and education_formset.is_valid()
                and experience_formset.is_valid()
                and project_formset.is_valid()
        ):
            user_data = user_form.cleaned_data
            education_data = [
                form.cleaned_data
                for form in education_formset if form.cleaned_data
            ]

            # TODO: TEST: 2 form datasından biri valid, diğeri valid olmazsa ne yapıyor
            # TODO: Date formatları tex'de Oct 2019 yerine 2019-10-01 olarak gözüküyor

            experience_data = self._split_experience_description(experience_formset)
            project_data = [
                form.cleaned_data
                for form in project_formset if form.cleaned_data
            ]

            base_tex_template = self.request.POST.get(
                'base_tex_template', settings.LATEX_SETTINGS['DEFAULT_TEMPLATE']
            )
            output_tex = (
                    settings.LATEX_SETTINGS['TEMP_DIR']
                  / f"resume_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tex"
            )
            tex_context = {
                'user_data': user_data,
                'education_data': education_data,
                'experience_data': experience_data,
                'project_data': project_data,
                'generation_date': datetime.now().strftime('%Y-%m-%d'),
            }
            try:
                tex_file = latex_handler.render_tex_template(
                    template_name=base_tex_template,
                    context=tex_context,
                    output_path=output_tex
                )

                pdf_file_path = TexToPdfConverter(tex_file).render_pdf()

                response = FileResponse(
                    open(pdf_file_path, 'rb'),
                    content_type='application/pdf'
                )
                response[ 'Content-Disposition'] = 'inline; filename="resume.pdf"'

                os.remove(pdf_file_path)
                return response

            except Exception as e:
                messages.error(self.request, f"Failed to render PDF: {str(e)}")
                return redirect('resume:resume_form')

        context = {
            'user_form': user_form,
            'education_formset': education_formset,
            'experience_formset': experience_formset,
            'project_formset': project_formset,
        }
        return self.render_to_response(context)


class TestView(TemplateView):
    """
    This view is used for test process of ResumeFormView
    """
    template_name = "test_form.html"

    def get(self, *args, **kwargs):
        test_formset = TestFormSet(
            initial=[
                {
                    'title': 'Test',
                    'start_date': '2023-01',
                    'end_date': '2023-05',
                    'description': 'Implemented Backend Api',
                }
            ],
            prefix='test'
        )
        context = {'test_formset': test_formset}
        return self.render_to_response(context)

    @staticmethod
    def _split_test_description(formset):
        test_data = list()
        for form in formset:
            if form.is_valid():
                cleaned_data = form.cleaned_data
                cleaned_data['description'] = list(
                    filter(None, map(
                        str.strip,
                        cleaned_data.get('description', '').split('\n')
                    )
                           )
                )
                test_data.append(cleaned_data)
        return test_data


    def post(self, *args, **kwargs):
        test_formset = TestFormSet(
            self.request.POST, prefix='test')
        base_tex_template = 'test.tex'

        if test_formset.is_valid():
            test_data = self._split_test_description(test_formset)

            output_tex = settings.LATEX_SETTINGS['TEMP_DIR'] / f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tex"

            context = {
                'test_data': test_data,
                'generation_date': datetime.now().strftime('%Y-%m-%d')
            }

            try:
                tex_file = latex_handler.render_tex_template(
                    base_tex_template, context, output_tex
                )

                pdf_file_path = TexToPdfConverter(tex_file).render_pdf()

                response = FileResponse(
                    open(pdf_file_path, 'rb'),
                    content_type='application/pdf'
                )
                response[ 'Content-Disposition'] = 'inline; filename="test.pdf"'

                os.remove(pdf_file_path)
                return response

            except Exception as e:
                messages.error(self.request, f"Failed to render PDF: {str(e)}")
                return redirect('resume:test_form')

        context = {
            'test_formset': test_formset
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
    field_id = request.POST.get('field_id')

    if field_id:
        form_index_match = re.search(fr"id_{prefix}-(\d+)-{field}", field_id)
        form_index = form_index_match.group(1)
        field_value = request.POST.get(
            request.POST.get('field_id').strip('id_')
        )

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
        prefix='experience',
        field='description',
        enhance_function=enhance_resume_experience
    )


@require_http_methods(["POST"])
def enhance_project(request):
    return enhance_field(
        request,
        prefix='project',
        field='description',
        enhance_function=enhance_project_description
    )


@require_http_methods(["POST"])
def enhance_test_form(request):
    return enhance_field(
        request,
        prefix='test',
        field='description',
        enhance_function=enhance_resume_experience
    )


@require_http_methods(["POST"])
def preview_test_form(request):
    if request.method == "POST":
        test_formset = TestFormSet(request.POST, prefix='test')
        if test_formset.is_valid():
            test_data = [
                form.cleaned_data
                for form in test_formset if form.cleaned_data
            ]

            rendered_html = render(
                request,
                template_name='test_preview.html',
                context={'test_formset': test_data}
            ).content.decode('utf-8')
            return JsonResponse({'html': rendered_html})
    return JsonResponse({'error': 'Invalid request'}, status=400)


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
                cleaned_data['description'] = list(
                    filter(None, map(
                        str.strip,
                        cleaned_data.get('description', '').split('\n')
                    )
                           )
                )
                experience_data.append(cleaned_data)
        return experience_data

    if request.method == "POST":
        user_form = UserInfoForm(request.POST)
        education_formset = EducationFormSet(request.POST, prefix='education')
        experience_formset = ExperienceFormSet(request.POST, prefix='experience')
        project_formset = ProjectFormSet(request.POST, prefix='project')

        if (
                user_form.is_valid()
                and education_formset.is_valid()
                and experience_formset.is_valid()
                and project_formset.is_valid()
        ):
            user_data = user_form.cleaned_data
            education_data = [
                form.cleaned_data
                for form in education_formset if form.cleaned_data
            ]
            experience_data = split_experience_description(experience_formset)
            project_data = [
                form.cleaned_data
                for form in project_formset if form.cleaned_data
            ]

            context = {
                'user_data': user_data,
                'education_data': education_data,
                'experience_data': experience_data,
                'project_data': project_data,
                'generation_date': datetime.now().strftime('%Y-%m-%d'),
            }
            template_name = settings.TEX_PREVIEW_HTML_MAP.get(
                settings.LATEX_SETTINGS['DEFAULT_TEMPLATE'], None)
            if not template_name:
                return JsonResponse({'error': 'Selected tex does not map any html preview'}, status=400)

            rendered_html = render(
                request,
                template_name=template_name,
                context=context
            ).content.decode('utf-8')
            return JsonResponse({'html': rendered_html})
    return JsonResponse({'error': 'Invalid request'}, status=400)