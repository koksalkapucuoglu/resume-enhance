import re

from django.forms import formset_factory
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from resume.forms import (
    UserInfoForm, EducationForm, ExperienceForm, ProjectForm
)
from resume.openai_engine import enhance_resume_experience, \
    enhance_project_description

EducationFormSet = formset_factory(EducationForm, extra=0)
ExperienceFormSet = formset_factory(ExperienceForm, extra=0)
ProjectFormSet = formset_factory(ProjectForm, extra=0)


class ResumeFormView(TemplateView):
    template_name = "resume_form.html"

    def get(self, *args, **kwargs):
        education_formset = EducationFormSet(prefix='education')
        experience_formset = ExperienceFormSet(prefix='experience')
        project_formset = ProjectFormSet(prefix='project')
        user_form = UserInfoForm()

        context = {
            'user_form': user_form,
            'education_formset': education_formset,
            'experience_formset': experience_formset,
            'project_formset': project_formset,
        }
        return self.render_to_response(context)

    def post(self, *args, **kwargs):
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
            pass
            # Process the form data here

        context = {
            'user_form': user_form,
            'education_formset': education_formset,
            'experience_formset': experience_formset,
            'project_formset': project_formset,
        }
        return self.render_to_response(context)


class EducationView(TemplateView):
    """
    This view is used for test process
    """
    template_name = "education_form.html"

    def get(self, *args, **kwargs):
        education_formset = EducationFormSet(
            initial=[
                {
                    'school': 'Init School',
                    'degree': 'Init Degree',
                    'field_of_study': 'Developed backend api for air control system',
                }
            ],
            prefix='education'
        )
        context = {'education_formset': education_formset}
        return self.render_to_response(context)

    def post(self, *args, **kwargs):
        education_formset = EducationFormSet(
            self.request.POST, prefix='education')

        if education_formset.is_valid():
            field_of_study = education_formset.cleaned_data[0].get(
                'field_of_study')
            enhanced_message = enhance_resume_experience(field_of_study)
            education_formset.cleaned_data[0][
                'field_of_study'] = enhanced_message
            education_formset = EducationFormSet(
                initial=education_formset.cleaned_data,
                prefix='education'
            )

        context = {'education_formset': education_formset}
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
def enhance_education(request):
    if request.method == "POST":
        form_index, field_value = get_field_value(request, prefix='education', field='field_of_study')
        if form_index and field_value:
            enhanced_text = enhance_resume_experience(field_value)
            field_of_study_html = f"""
            <textarea name="education-{form_index}-field_of_study" cols="40" rows="10" 
            class="textarea form-control" id="id_education-{form_index}-field_of_study">{enhanced_text}</textarea>
            """
            return HttpResponse(field_of_study_html)

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