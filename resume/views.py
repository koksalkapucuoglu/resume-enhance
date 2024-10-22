from django.shortcuts import render
from .forms import UserInfoForm, EducationForm, ExperienceForm, ProjectForm
from django.forms import formset_factory


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
