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
                'Invalid data format. It should be YYYY-MM'
            )


class YearOnlyInput(forms.NumberInput):
    """Custom widget for year-only input."""
    def __init__(self, attrs=None):
        default_attrs = {
            'min': '1950',
            'max': '2100',
            'placeholder': 'YYYY',
            'style': 'width: 100px;'
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)


class YearOnlyField(forms.IntegerField):
    """Field that accepts only a year (integer)."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('min_value', 1950)
        kwargs.setdefault('max_value', 2100)
        kwargs.setdefault('widget', YearOnlyInput())
        super().__init__(*args, **kwargs)
    
    def to_python(self, value):
        if not value:
            return None
        
        # Handle string inputs (e.g., "2020" or "2020-05")
        if isinstance(value, str):
            # Extract just the year if month included
            value = value.split('-')[0] if '-' in value else value
            try:
                return int(value)
            except (ValueError, TypeError):
                raise forms.ValidationError(
                    'Invalid year format. Please enter a valid year (e.g., 2020)'
                )
        
        return super().to_python(value)


class UserInfoForm(forms.Form):
    full_name = forms.CharField(label='Full Name')
    email = forms.EmailField(label='Email')
    phone = forms.CharField(
        max_length=20,
        label='Phone Number',
        required=False
    )
    github = forms.URLField(label='Github Profile Url', required=False)
    linkedin = forms.URLField(label='Linkedin Profile Url', required=False)
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
    start_year = YearOnlyField(label='Start Year')
    end_year = YearOnlyField(label='End (Expected) Year')


class ExperienceForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        label="Role Title",
    )
    start_date = MonthYearField(
        widget=MonthYearDateInput(format='%Y-%m'),
        label='Start Date',
    )
    end_date = MonthYearField(
        widget=MonthYearDateInput(format='%Y-%m'),
        label='End Date',
        required=False
    )
    current_role = forms.BooleanField(
        label="I am currently working in this role",
        required=False
    )
    company = forms.CharField(
        max_length=255,
        label="Company Name"
    )
    description = forms.CharField(
        widget=forms.Textarea,
        label="Description"
    )


class ProjectForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        label="Name"
    )
    description = forms.CharField(
        widget=forms.Textarea,
        label="Description"
    )
    link = forms.URLField(label='Link', required=False)