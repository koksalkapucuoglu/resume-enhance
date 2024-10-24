from django.forms import formset_factory
from django.views.generic import TemplateView

from resume.forms import (
    UserInfoForm, EducationForm, ExperienceForm, ProjectForm
)


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
                    'field_of_study': 'Init Field of Study',
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
            pass
            #return redirect(reverse_lazy("resume:education"))

        context = {'education_formset': education_formset}
        return self.render_to_response(context)
