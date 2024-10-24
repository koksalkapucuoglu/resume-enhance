from django.shortcuts import render
from django.forms import formset_factory
from django.views.generic import TemplateView
from django.urls import reverse_lazy
from django.shortcuts import redirect

from .forms import UserInfoForm, EducationForm, ExperienceForm, ProjectForm


EducationFormSet = formset_factory(EducationForm, extra=0)


def user_info(request):
    EducationFormSet = formset_factory(EducationForm, extra=0)
    ExperienceFormSet = formset_factory(ExperienceForm, extra=0)
    ProjectFormSet = formset_factory(ProjectForm, extra=0)

    if request.method == 'POST':
        user_form = UserInfoForm(request.POST)
        education_formset = EducationFormSet(request.POST)
        experience_formset = ExperienceFormSet(request.POST)
        project_formset = ProjectFormSet(request.POST)

        if (user_form.is_valid()
                and education_formset.is_valid()
                and experience_formset.is_valid()
                and project_formset.is_valid()
        ):
            # Process the form data here
            pass
    else:
        user_form = UserInfoForm()
        education_formset = EducationFormSet()
        experience_formset = ExperienceFormSet()
        project_formset = ProjectFormSet()

    return render(request, 'resume_form.html', {
        'user_form': user_form,
        'education_formset': education_formset,
        'experience_formset': experience_formset,
        'project_formset': project_formset,
    })


class EducationView(TemplateView):
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
