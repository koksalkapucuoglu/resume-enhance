from django import forms
from datetime import datetime

class MonthYearDateInput(forms.DateInput):
    input_type = 'month'


class MonthYearField(forms.Field):
    def to_python(self, value):
        if not value:
            return None

        try:
            return datetime.strptime(value, '%Y-%m').date()
        except (ValueError, TypeError):
            raise forms.ValidationError(
                'Invalid data format. It should be YYYY-MM')



class UserInfoForm(forms.Form):
    full_name = forms.CharField(label='Full Name')
    email = forms.EmailField(label='Email')
    phone = forms.CharField(
        max_length=20,
        label='Phone Number',
        required=False
    )
    github = forms.URLField(label='Github Profile Url', required=False)
    linkedin = forms.URLField(label='Linkedin Profile Url', required=False,)
    skills = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'rows': 1,
                'placeholder': 'Enter your skills separated by commas...'})
    )


class EducationForm(forms.Form):
    school = forms.CharField(max_length=100, label='School')
    degree = forms.CharField(max_length=100, label='Degree')
    field_of_study = forms.CharField(max_length=100, label='Field of Study')
    start_date = MonthYearField(
        widget=MonthYearDateInput(format='%Y-%m'),
        label='Start Date')
    end_date = MonthYearField(
        widget=MonthYearDateInput(format='%Y-%m'),
        label='End(Expected) Date'
    )


class ExperienceForm(forms.Form):
    title = forms.CharField(max_length=255, label="Role Title")
    start_date = forms.DateField(
        widget=MonthYearDateInput(format='%Y-%m'),
        label='Start Date'
    )
    end_date = forms.DateField(
        widget=MonthYearDateInput(format='%Y-%m'),
        label='End Date',
        required=False
    )
    current_role = forms.BooleanField(
        required=False,
        label="I am currently working in this role"
    )
    company = forms.CharField(max_length=255, label="Company Name")
    description = forms.CharField(widget=forms.Textarea, label="Description")


class ProjectForm(forms.Form):
    name = forms.CharField(max_length=255, label="Name")
    description = forms.CharField(widget=forms.Textarea, label="Description")
    link = forms.URLField(label='Link', required=False)
