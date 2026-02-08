"""
Authentication views for user signup and profile.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views import View


class SignupForm(UserCreationForm):
    """Custom signup form with email field."""
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'you@example.com'
        })
    )
    
    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class SignupView(View):
    """User registration view."""
    
    def get(self, request):
        """Display signup form."""
        if request.user.is_authenticated:
            return redirect('resume:index')
        
        form = SignupForm()
        return render(request, 'registration/signup.html', {'form': form})
    
    def post(self, request):
        """Process signup form."""
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)  # Auto-login after signup
            return redirect('resume:index')
        
        return render(request, 'registration/signup.html', {'form': form})


@method_decorator(login_required, name='dispatch')
class ProfileView(View):
    """User profile view with password change."""
    
    def get(self, request):
        """Display profile page."""
        password_form = PasswordChangeForm(request.user)
        return render(request, 'registration/profile.html', {
            'password_form': password_form
        })
    
    def post(self, request):
        """Handle password change."""
        password_form = PasswordChangeForm(request.user, request.POST)
        
        if password_form.is_valid():
            user = password_form.save()
            # Keep user logged in after password change
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password has been changed successfully!')
            return redirect('profile')
        
        return render(request, 'registration/profile.html', {
            'password_form': password_form
        })
